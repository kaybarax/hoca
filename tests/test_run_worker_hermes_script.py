from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-worker-hermes.sh"
HOCA_ROOT = SCRIPT.parents[1]


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def make_fake_ollama(fake_bin: Path) -> None:
    ollama = fake_bin / "ollama"
    ollama.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" != "list" ]]; then exit 2; fi\n'
        "cat <<'EOF'\n"
        "NAME ID SIZE MODIFIED\n"
        "qwen-14b-pro abc 1GB now\n"
        "EOF\n",
        encoding="utf-8",
    )
    ollama.chmod(ollama.stat().st_mode | stat.S_IXUSR)

    curl = fake_bin / "curl"
    curl.write_text("#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n", encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)


def make_fake_openhands(fake_bin: Path) -> None:
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "echo 'OpenHands fake run complete.'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)


def run_script(
    *args: str,
    extra_env: dict[str, str] | None = None,
    fake_bin: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HOCA_ROOT)
    env["HOCA_PYTHON"] = sys.executable
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


def write_task_spec(run_dir: Path) -> Path:
    spec = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "repo_root": str(run_dir.parents[2]),
        "base_branch": "main",
        "task_branch": "feat/demo",
        "issue_id": None,
        "raw_request": "Update README",
        "goal": "Update README with install steps",
        "non_goals": ["Do not commit changes"],
        "expected_areas": ["README.md"],
        "acceptance_criteria": ["README documents install steps"],
        "test_commands": ["pytest"],
        "risk_level": "low",
        "requires_human_approval": False,
        "max_total_rounds": 3,
        "models": {
            "manager": "slot-a",
            "worker": "slot-b",
            "reviewer": "slot-c",
            "fallback": "slot-a",
        },
        "sandbox": {"enabled": True, "network_mode": "offline"},
    }
    path = run_dir / "task-spec.json"
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return path


def make_fake_worker_hermes(fake_bin: Path) -> None:
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'RUN_DIR="${HERMES_TEST_RUN_DIR:?}"\n'
        'mkdir -p "$RUN_DIR/attempts" "$RUN_DIR/logs"\n'
        'cat > "$RUN_DIR/attempts/worker-attempt-1.json" <<EOF\n'
        '{"schema_version":1,"run_id":"run-shell","round":1,"role":"worker",'
        '"status":"completed","changed_files":[],"summary":["ok"],'
        '"commands_run":["run-worker-hermes.sh"],"tests_run":[],"known_risks":[],'
        '"blocked_reason":null,"artifact_paths":{}}\n'
        "EOF\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IXUSR)


def test_script_profile_mode_writes_worker_attempt(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    make_fake_worker_hermes(fake_bin)
    hermes_home = tmp_path / "hermes-home"
    (hermes_home / "profiles" / "hoca-worker").mkdir(parents=True)

    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-shell"
    run_dir.mkdir(parents=True)
    task_spec_path = write_task_spec(run_dir)

    result = run_script(
        str(project),
        str(task_spec_path),
        str(run_dir),
        "1",
        extra_env={"HERMES_HOME": str(hermes_home), "HERMES_TEST_RUN_DIR": str(run_dir)},
        fake_bin=fake_bin,
    )

    assert result.returncode == 0, result.stderr
    attempt = run_dir / "attempts" / "worker-attempt-1.json"
    assert attempt.is_file()
    data = json.loads(attempt.read_text(encoding="utf-8"))
    assert data["status"] in {"completed", "failed", "blocked"}
    assert data["round"] == 1


def test_script_fails_without_hermes(tmp_path: Path) -> None:
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
        extra_env={"PATH": f"{empty_bin}:/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "hermes command not found" in result.stderr


def test_script_documents_required_behavior() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "hoca.worker_hermes" in script
    assert "--repair-brief" in script
    removed_profile_toggle = "HOCA_USE_" + "HERMES_PROFILES"
    assert removed_profile_toggle not in script
    assert "Not a Git repository" in script
