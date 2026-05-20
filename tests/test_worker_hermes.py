from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from hoca.contracts import HocaAttemptReport, HocaRoleModelSelection, HocaSandboxPolicy, HocaTaskSpec
from hoca.run_artifacts import record_worker_attempt
from hoca.run_layout import ensure_run_layout, worker_attempt_path
from hoca.worker_hermes import (
    build_legacy_openhands_task,
    build_worker_hermes_prompt,
    load_task_spec,
    run_worker_hermes,
    verify_profile_prerequisites,
)


def sample_task_spec(**overrides: object) -> HocaTaskSpec:
    base = HocaTaskSpec(
        run_id="run-test",
        repo_root="/tmp/project",
        base_branch="main",
        task_branch="feat/demo",
        issue_id=None,
        raw_request="Update README",
        goal="Update README with installation steps",
        non_goals=["Do not commit changes"],
        expected_areas=["README.md"],
        acceptance_criteria=["README documents install steps"],
        test_commands=["pytest"],
        risk_level="low",
        requires_human_approval=False,
        max_total_rounds=3,
        models=HocaRoleModelSelection(
            manager="slot-a",
            worker="slot-b",
            reviewer="slot-c",
            fallback="slot-a",
        ),
        sandbox=HocaSandboxPolicy(enabled=True, network_mode="offline"),
    )
    data = base.to_dict()
    data.update(overrides)
    return HocaTaskSpec.from_dict(data)


def test_build_worker_hermes_prompt_excludes_secret_values() -> None:
    spec = sample_task_spec(
        goal="Fix auth",
        non_goals=["Do not expose API_KEY=super-secret-token"],
    )
    prompt = build_worker_hermes_prompt(
        spec=spec,
        project_path=Path("/tmp/project"),
        run_dir=Path("/tmp/project/.hoca-runtime/runs/run-test"),
        round_number=1,
        task_spec_path=Path("/tmp/project/.hoca-runtime/runs/run-test/task-spec.json"),
        repair_brief="Repair token=abc123 before continuing",
    )

    assert "super-secret-token" not in prompt
    assert "abc123" not in prompt
    assert "[redacted: possible secret]" in prompt
    assert "run-openhands-task.sh" in prompt
    assert "worker-attempt-" in prompt
    assert "Manager-owned Git lifecycle only" in prompt
    assert "git add, git commit, git push" in prompt
    assert "Do not stage, commit, push" in prompt


def test_build_legacy_openhands_task_prefers_repair_brief() -> None:
    spec = sample_task_spec(goal="Original goal")
    repair = "Fix failing tests in src/app.py"
    assert build_legacy_openhands_task(spec=spec, repair_brief=repair) == repair
    assert build_legacy_openhands_task(spec=spec) == spec.goal


def test_load_task_spec_reads_json(tmp_path: Path) -> None:
    spec_path = tmp_path / "task-spec.json"
    spec = sample_task_spec()
    spec_path.write_text(spec.to_json(), encoding="utf-8")

    loaded = load_task_spec(spec_path)
    assert loaded.goal == spec.goal
    assert loaded.run_id == "run-test"


def test_verify_profile_prerequisites_requires_hermes_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setattr("hoca.worker_hermes.hermes_installed", lambda: False)
    with pytest.raises(RuntimeError, match="hermes command not found"):
        verify_profile_prerequisites(hermes_home=hermes_home)

    monkeypatch.setattr("hoca.worker_hermes.hermes_installed", lambda: True)
    with pytest.raises(RuntimeError, match="hoca-worker"):
        verify_profile_prerequisites(hermes_home=hermes_home)


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
        'TASK_ARG=""\n'
        'prev=""\n'
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "--task" ]]; then TASK_ARG="$arg"; fi\n'
        '  prev="$arg"\n'
        "done\n"
        'if [[ -n "$TASK_ARG" ]]; then\n'
        '  echo "$TASK_ARG" > README.md\n'
        "fi\n"
        "echo 'OpenHands fake run complete.'\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def test_run_worker_hermes_legacy_mode_writes_attempt_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    make_fake_openhands(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOCA_USE_SANDBOX", "false")

    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    spec = sample_task_spec(repo_root=str(project))
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(spec.to_json(), encoding="utf-8")

    result = run_worker_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
        use_hermes_profiles=False,
    )

    assert result.mode == "legacy"
    assert result.exit_code == 0
    assert result.worker_attempt_path == worker_attempt_path(run_dir, 1)
    report = HocaAttemptReport.from_json(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report.status == "completed"
    assert report.round == 1
    assert (run_dir / "openhands-task-round-1.txt").is_file()


def test_run_worker_hermes_legacy_failure_still_writes_attempt_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    openhands = fake_bin / "openhands"
    openhands.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--help" ]]; then\n'
        '  echo "openhands --headless --task --override-with-envs --json"\n'
        "  exit 0\n"
        "fi\n"
        "exit 9\n",
        encoding="utf-8",
    )
    openhands.chmod(openhands.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOCA_USE_SANDBOX", "false")

    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    spec = sample_task_spec(repo_root=str(project))
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(spec.to_json(), encoding="utf-8")

    result = run_worker_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
        use_hermes_profiles=False,
    )

    assert result.exit_code == 9
    report = HocaAttemptReport.from_json(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report.status == "failed"


def test_run_worker_hermes_profile_mode_invokes_hermes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_fake_ollama(fake_bin)
    make_fake_openhands(fake_bin)

    hermes_home = tmp_path / "hermes-home"
    profile_dir = hermes_home / "profiles" / "hoca-worker"
    profile_dir.mkdir(parents=True)

    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "-p" && "${2:-}" == "hoca-worker" ]]; then\n'
        "  shift 2\n"
        "fi\n"
        'while [[ $# -gt 0 ]]; do\n'
        '  case "$1" in\n'
        "    -z)\n"
        '      PROMPT="${2:-}"\n'
        "      shift 2\n"
        "      ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'RUN_DIR="${HERMES_TEST_RUN_DIR:?}"\n'
        'ROUND="${HERMES_TEST_ROUND:?}"\n'
        'mkdir -p "$RUN_DIR/attempts"\n'
        'cat > "$RUN_DIR/attempts/worker-attempt-${ROUND}.json" <<EOF\n'
        "{\n"
        '  "schema_version": 1,\n'
        '  "run_id": "run-test",\n'
        '  "round": '"${HERMES_TEST_ROUND}"',\n'
        '  "role": "worker",\n'
        '  "status": "completed",\n'
        '  "changed_files": [],\n'
        '  "summary": ["Hermes worker completed"],\n'
        '  "commands_run": ["run-openhands-task.sh"],\n'
        '  "tests_run": [],\n'
        '  "known_risks": [],\n'
        '  "blocked_reason": null,\n'
        '  "artifact_paths": {}\n'
        "}\n"
        "EOF\n"
        'printf "%s\\n" "$PROMPT" > "$RUN_DIR/logs/worker-hermes-invoked.txt"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HOCA_USE_SANDBOX", "false")

    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    spec = sample_task_spec(repo_root=str(project))
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(spec.to_json(), encoding="utf-8")

    monkeypatch.setenv("HERMES_TEST_RUN_DIR", str(run_dir))
    monkeypatch.setenv("HERMES_TEST_ROUND", "2")

    result = run_worker_hermes(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=2,
        repair_brief="Fix README formatting only.",
        use_hermes_profiles=True,
        hermes_home=hermes_home,
    )

    assert result.mode == "profile"
    assert result.exit_code == 0
    assert result.worker_attempt_path == worker_attempt_path(run_dir, 2)
    invoked = (run_dir / "logs" / "worker-hermes-invoked.txt").read_text(encoding="utf-8")
    assert "Fix README formatting only." in invoked
    assert "super-secret" not in invoked.lower()
    assert (run_dir / "logs" / "worker-hermes-stdout.txt").is_file()
    assert (run_dir / "logs" / "worker-hermes-stderr.txt").is_file()
    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"


def test_record_worker_attempt_monitor_stopped_produces_blocked_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)
    monitor = {
        "exit_code": 1,
        "stop_reason": "secret_detected",
        "events": [
            {"type": "secret_access", "detail": "read .env file"},
        ],
    }
    (run_dir / "monitor-result.json").write_text(json.dumps(monitor), encoding="utf-8")

    path = record_worker_attempt(run_dir, round_number=1, status="blocked")
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert report.status == "blocked"
    assert report.blocked_reason == "secret_detected"
    assert any("Monitor stop reason: secret_detected" in s for s in report.summary)
    assert any("secret_access" in s for s in report.summary)


def test_record_worker_attempt_failed_openhands_produces_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)
    (run_dir / "openhands-error.txt").write_text("Segmentation fault\n", encoding="utf-8")

    path = record_worker_attempt(run_dir, round_number=2, status="failed")
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert report.status == "failed"
    assert report.round == 2
    assert report.blocked_reason == "Segmentation fault"
    assert path == worker_attempt_path(run_dir, 2)


def test_record_worker_attempt_captures_existing_command_and_log_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)
    (run_dir / "openhands-stderr.log").write_text("stderr\n", encoding="utf-8")
    (run_dir / "openhands-exit-code.txt").write_text("1\n", encoding="utf-8")
    (run_dir / "failed-command.txt").write_text("pytest tests/test_api.py\n", encoding="utf-8")
    (run_dir / "tests-summary.md").write_text(
        "# Test Summary\n\n"
        "- **Status**: failed\n"
        "- **Command**: `pytest tests/test_api.py`\n"
        "- **Failed command**: `pytest tests/test_api.py`\n",
        encoding="utf-8",
    )
    (run_dir / "tests-output.log").write_text("failed output\n", encoding="utf-8")
    (run_dir / "changed-files-after-openhands.txt").write_text("src/api.py\n", encoding="utf-8")

    path = record_worker_attempt(run_dir, round_number=1, status="failed")
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert report.changed_files == ["src/api.py"]
    assert report.tests_run == ["pytest tests/test_api.py"]
    assert report.artifact_paths["openhands_stderr"].endswith("openhands-stderr.log")
    assert report.artifact_paths["failed_command"].endswith("failed-command.txt")
    assert report.artifact_paths["tests_summary"].endswith("tests-summary.md")
    assert report.artifact_paths["tests_output"].endswith("tests-output.log")
    assert report.artifact_paths["changed_files_after_openhands"].endswith(
        "changed-files-after-openhands.txt"
    )


def test_record_worker_attempt_redacts_secrets_from_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)

    path = record_worker_attempt(
        run_dir,
        round_number=1,
        status="completed",
        summary=["Fixed bug where API_KEY=sk-live-abc123 was exposed"],
    )
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert "sk-live-abc123" not in " ".join(report.summary)
    assert "[redacted: possible secret]" in " ".join(report.summary)


def test_record_worker_attempt_redacts_secret_like_blocked_reason(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)
    (run_dir / "openhands-error.txt").write_text(
        "OpenHands failed with token=secret-value\n",
        encoding="utf-8",
    )

    path = record_worker_attempt(run_dir, round_number=1, status="failed")
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert "secret-value" not in str(report.blocked_reason)
    assert report.blocked_reason == "OpenHands failed with [redacted: possible secret]"


def test_record_worker_attempt_profile_mode_captures_log_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "worker-hermes-stdout.txt").write_text("stdout output\n", encoding="utf-8")
    (logs_dir / "worker-hermes-stderr.txt").write_text("stderr output\n", encoding="utf-8")

    path = record_worker_attempt(run_dir, round_number=1, status="completed", mode="profile")
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert "run-worker-hermes.sh" in report.commands_run
    assert "run-openhands-task.sh" in report.commands_run
    assert "worker_hermes_stdout" in report.artifact_paths
    assert "worker_hermes_stderr" in report.artifact_paths


def test_record_worker_attempt_git_fallback_for_changed_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "new_file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "new_file.txt"], cwd=project, check=True)

    run_dir = tmp_path / "run"
    ensure_run_layout(run_dir)

    path = record_worker_attempt(
        run_dir, round_number=1, status="completed", project_path=project,
    )
    report = HocaAttemptReport.from_json(path.read_text(encoding="utf-8"))

    assert "new_file.txt" in report.changed_files
