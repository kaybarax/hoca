from __future__ import annotations

from pathlib import Path

import pytest

from hoca.config import HocaConfig
from hoca.sandbox_doctor import (
    _resource_limit_lines,
    _script_static_checks,
    sandbox_doctor_lines,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _messages(lines: list[tuple[str, str]]) -> list[str]:
    return [message for _, message in lines]


def test_sandbox_doctor_warns_on_host_execution() -> None:
    lines = sandbox_doctor_lines(HocaConfig(use_sandbox=False), root=REPO_ROOT)
    messages = _messages(lines)
    assert any("Host execution enabled" in message for message in messages)
    assert any("HOCA_USE_SANDBOX=false" in message for message in messages)


def test_sandbox_doctor_reports_offline_network_mode() -> None:
    lines = sandbox_doctor_lines(HocaConfig(network_mode="offline"), root=REPO_ROOT)
    assert any("offline" in message.lower() for _, message in lines)


def test_sandbox_doctor_warns_on_full_network_mode() -> None:
    lines = sandbox_doctor_lines(HocaConfig(network_mode="full"), root=REPO_ROOT)
    messages = _messages(lines)
    assert any("network mode: full" in message.lower() for message in messages)
    assert any("unrestricted egress" in message.lower() for message in messages)


def test_sandbox_doctor_fails_on_invalid_network_mode() -> None:
    lines = sandbox_doctor_lines(HocaConfig(network_mode="wide-open"), root=REPO_ROOT)
    assert any(status == "fail" for status, _ in lines)


def test_script_static_checks_flag_docker_socket_mount(tmp_path: Path) -> None:
    script = tmp_path / "run-openhands-sandboxed.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'docker run -v /var/run/docker.sock:/var/run/docker.sock "$@"',
            ]
        ),
        encoding="utf-8",
    )
    lines = _script_static_checks(tmp_path)
    assert any("mounts Docker socket" in message for _, message in lines)


def test_script_static_checks_passes_hoca_sandbox_scripts() -> None:
    lines = _script_static_checks(REPO_ROOT / "scripts")
    messages = _messages(lines)
    assert all(
        "GITHUB_TOKEN" not in message or "does not forward" in message for message in messages
    )
    assert any("does not mount Docker socket" in message for message in messages)


def test_resource_limit_lines_require_memory_and_pids() -> None:
    lines = _resource_limit_lines(REPO_ROOT / "scripts", memory="8g", pids="512")
    messages = _messages(lines)
    assert any("memory limit configured" in message.lower() for message in messages)
    assert any("pid limit configured" in message.lower() for message in messages)


def test_resource_limit_lines_warn_on_empty_limits(tmp_path: Path) -> None:
    script = tmp_path / "run-openhands-sandboxed.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'docker run --memory="${HOCA_SANDBOX_MEMORY}" --pids-limit="${HOCA_SANDBOX_PIDS}"',
            ]
        ),
        encoding="utf-8",
    )
    lines = _resource_limit_lines(tmp_path, memory="", pids="")
    messages = _messages(lines)
    assert any("HOCA_SANDBOX_MEMORY is empty" in message for message in messages)
    assert any("HOCA_SANDBOX_PIDS is empty" in message for message in messages)


def test_sandbox_doctor_image_user_non_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:3] == ["docker", "image", "inspect"] and args[-1] == "hoca-sandbox:latest":
            if "--format" in args:
                return pytest.importorskip("subprocess").CompletedProcess(
                    args=args, returncode=0, stdout="worker\n", stderr=""
                )
            return pytest.importorskip("subprocess").CompletedProcess(
                args=args, returncode=0, stdout="[]", stderr=""
            )
        if args[:3] == ["docker", "info"]:
            return pytest.importorskip("subprocess").CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        return pytest.importorskip("subprocess").CompletedProcess(
            args=args, returncode=1, stdout="", stderr="missing"
        )

    monkeypatch.setattr(
        "hoca.sandbox_doctor.shutil.which",
        lambda cmd: "/usr/bin/docker" if cmd == "docker" else None,
    )
    monkeypatch.setattr("hoca.sandbox_doctor.subprocess.run", fake_run)

    lines = sandbox_doctor_lines(HocaConfig(use_sandbox=True), root=REPO_ROOT)
    messages = _messages(lines)
    assert any("default user is non-root" in message for message in messages)
    assert any("Sandbox image is available" in message for message in messages)


def test_sandbox_doctor_image_root_user_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args, **kwargs):
        import subprocess

        if args[:3] == ["docker", "image", "inspect"] and "--format" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="root\n", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(
        "hoca.sandbox_doctor.shutil.which",
        lambda cmd: "/usr/bin/docker" if cmd == "docker" else None,
    )
    monkeypatch.setattr("hoca.sandbox_doctor.subprocess.run", fake_run)

    lines = sandbox_doctor_lines(HocaConfig(use_sandbox=True), root=REPO_ROOT)
    assert any(status == "fail" and "defaults to root" in message for status, message in lines)
