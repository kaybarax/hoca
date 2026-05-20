from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoca.doctor import DoctorReport, parse_doctor_output, run_doctor

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parse_doctor_output_extracts_tagged_checks() -> None:
    checks = parse_doctor_output(
        "\n".join(
            [
                "HOCA Doctor",
                "[OK] git found: /usr/bin/git",
                "[WARN] .env not found.",
                "[FAIL] Docker is installed but the daemon is not running.",
                "Summary",
            ]
        )
    )

    assert [check.status for check in checks] == ["ok", "warn", "fail"]
    assert checks[0].message == "git found: /usr/bin/git"
    assert checks[1].message == ".env not found."
    assert checks[2].message == "Docker is installed but the daemon is not running."


def test_doctor_report_ok_requires_zero_exit_and_no_failures() -> None:
    report = DoctorReport(
        returncode=0,
        checks=parse_doctor_output("[OK] Ready\n[WARN] Optional thing missing\n"),
        stdout="",
        stderr="",
    )

    assert report.ok is True
    assert len(report.warnings) == 1
    assert report.failures == ()


def test_doctor_report_is_not_ok_when_failures_are_present() -> None:
    report = DoctorReport(
        returncode=0,
        checks=parse_doctor_output("[FAIL] gh not found\n"),
        stdout="",
        stderr="",
    )

    assert report.ok is False
    assert len(report.failures) == 1


def test_parse_doctor_output_extracts_openhands_capabilities() -> None:
    checks = parse_doctor_output(
        "\n".join(
            [
                "[OK] OpenHands supports --headless.",
                "[OK] OpenHands supports --task.",
                "[OK] OpenHands supports --override-with-envs.",
                "[OK] OpenHands supports --json.",
                "[WARN] OpenHands CLI help does not show optional --enable-browsing.",
                "[OK] OpenHands capabilities: headless,task,override-with-envs,json",
            ]
        )
    )

    caps_check = [c for c in checks if "capabilities:" in c.message]
    assert len(caps_check) == 1
    assert caps_check[0].status == "ok"
    assert "headless" in caps_check[0].message
    assert "enable-browsing" not in caps_check[0].message


def test_parse_doctor_output_includes_browsing_when_available() -> None:
    checks = parse_doctor_output(
        "[OK] OpenHands capabilities: headless,task,override-with-envs,json,enable-browsing\n"
    )

    caps_check = [c for c in checks if "capabilities:" in c.message]
    assert len(caps_check) == 1
    assert "enable-browsing" in caps_check[0].message


def test_doctor_output_includes_model_pool_section_when_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOCA_MODEL_1_NAME", "local-coder")
    monkeypatch.setenv("HOCA_MODEL_1_MODEL", "ollama/qwen-14b-pro")
    monkeypatch.setenv("HOCA_MODEL_1_API_KEY", "secret-key")
    monkeypatch.setenv("HOCA_FALLBACK_MODEL", "local-coder")

    from hoca.role_model_env import model_pool_doctor_lines
    from hoca.config import load_config

    lines = model_pool_doctor_lines(load_config())

    assert any("Model pool active" in message for _, message in lines)
    assert all("secret-key" not in message for _, message in lines)


def test_hoca_doctor_script_includes_sandbox_section() -> None:
    script = REPO_ROOT / "scripts" / "hoca-doctor.sh"
    content = script.read_text(encoding="utf-8")
    assert 'section "Sandbox"' in content
    assert "HOCA_USE_SANDBOX" in content
    assert "run-openhands-sandboxed.sh" in content


def test_run_doctor_invokes_shell_source_of_truth(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="[OK] HOCA Doctor completed with warnings.\n",
            stderr="",
        )

    monkeypatch.setattr("hoca.doctor.subprocess.run", fake_run)

    report = run_doctor()

    assert calls
    assert calls[0][0][0][0].endswith("scripts/hoca-doctor.sh")
    assert report.ok is True
    assert report.checks[0].message == "HOCA Doctor completed with warnings."
    assert "[OK] HOCA Doctor completed with warnings." in capsys.readouterr().out
