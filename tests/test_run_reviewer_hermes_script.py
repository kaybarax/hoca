from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_run_worker_hermes_script import (
    HOCA_ROOT,
    init_repo,
    make_fake_ollama,
    write_task_spec,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-reviewer-hermes.sh"


def make_fake_review_openhands(fake_bin: Path) -> None:
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "echo 'Review complete.'\n"
        "echo 'LGTM'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | 0o100)


def make_fake_profile_hermes(fake_bin: Path) -> None:
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "LLM_MODEL=%s\\n" "${LLM_MODEL:-}" > "$HERMES_CAPTURE_ENV"\n'
        'printf "HOCA_SKIP_ROLE_MODEL_RESOLUTION=%s\\n" "${HOCA_SKIP_ROLE_MODEL_RESOLUTION:-}" >> "$HERMES_CAPTURE_ENV"\n'
        'mkdir -p reviews logs\n'
        "cat > reviews/review-report-1.json <<'JSON'\n"
        "{\n"
        '  "schema_version": 1,\n'
        '  "run_id": "run-profile",\n'
        '  "round": 1,\n'
        '  "role": "reviewer",\n'
        '  "verdict": "LGTM",\n'
        '  "findings": [],\n'
        '  "pr_notes": {"summary": ["ok"], "known_followups": []}\n'
        "}\n"
        "JSON\n"
        "echo 'LGTM'\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | 0o100)


def run_script(
    *args: str,
    extra_env: dict[str, str] | None = None,
    fake_bin: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HOCA_ROOT)
    env["HOCA_PYTHON"] = sys.executable
    env["HOCA_USE_HERMES_PROFILES"] = "false"
    env["HOCA_USE_SANDBOX"] = "false"
    if fake_bin is not None:
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(SCRIPT), *args],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        cwd=HOCA_ROOT,
    )


def test_script_legacy_mode_writes_review_report(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    make_fake_review_openhands(fake_bin)
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-shell"
    run_dir.mkdir(parents=True)
    task_spec_path = write_task_spec(run_dir)

    result = run_script(
        str(project),
        str(task_spec_path),
        str(run_dir),
        "1",
        fake_bin=fake_bin,
    )

    assert result.returncode == 0, result.stderr
    report = run_dir / "reviews" / "review-report-1.json"
    assert report.is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "LGTM"


def test_script_fails_when_profile_mode_enabled_without_hermes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-profile"
    run_dir.mkdir(parents=True)
    task_spec_path = write_task_spec(run_dir)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()

    result = run_script(
        str(project),
        str(task_spec_path),
        str(run_dir),
        "1",
        extra_env={
            "PATH": f"{empty_bin}:/usr/bin:/bin",
            "HOCA_USE_HERMES_PROFILES": "true",
        },
    )

    assert result.returncode == 1
    assert "hermes command not found" in result.stderr


def test_profile_mode_pins_nested_reviewer_to_selected_model(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_profile_hermes(fake_bin)
    hermes_home = tmp_path / "hermes-home"
    (hermes_home / "profiles" / "hoca-reviewer").mkdir(parents=True)
    capture_env = tmp_path / "reviewer-env.txt"
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("changed\n", encoding="utf-8")
    run_dir = project / ".hoca-runtime" / "runs" / "run-profile"
    run_dir.mkdir(parents=True)
    task_spec_path = write_task_spec(run_dir)

    result = run_script(
        str(project),
        str(task_spec_path),
        str(run_dir),
        "1",
        extra_env={
            "HOCA_USE_HERMES_PROFILES": "true",
            "HERMES_HOME": str(hermes_home),
            "HOCA_REVIEWER_MODEL_NAME": "reviewer-cloud",
            "HOCA_REVIEWER_MODEL_MODEL": "deepseek/deepseek-v4-flash",
            "HOCA_REVIEWER_MODEL_API_KEY": "secret-reviewer",
            "HERMES_CAPTURE_ENV": str(capture_env),
        },
        fake_bin=fake_bin,
    )

    assert result.returncode == 0, result.stderr
    captured = capture_env.read_text(encoding="utf-8")
    assert "LLM_MODEL=deepseek/deepseek-v4-flash" in captured
    assert "HOCA_SKIP_ROLE_MODEL_RESOLUTION=true" in captured


def test_script_documents_required_behavior() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "hoca.reviewer_hermes" in script
    assert "HOCA_USE_HERMES_PROFILES" in script
    assert "Not a Git repository" in script
