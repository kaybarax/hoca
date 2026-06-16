from __future__ import annotations

import json
import subprocess
from pathlib import Path

from hoca.contracts import HocaRoleModelSelection, HocaSandboxPolicy, HocaTaskSpec
from hoca.run_layout import ensure_run_layout, worker_attempt_path
from hoca.worker_direct import (
    build_worker_direct_prompt,
    run_worker_direct,
    write_worker_direct_prompt,
)

MAC_HOME = "/" + "Users/example"


def sample_task_spec(**overrides: object) -> HocaTaskSpec:
    base = HocaTaskSpec(
        run_id="run-test",
        repo_root="/tmp/original",
        base_branch="main",
        task_branch="feat/direct",
        issue_id="42",
        raw_request="Update README",
        goal="Update README with install steps",
        non_goals=["Do not edit source code"],
        expected_areas=["README.md"],
        acceptance_criteria=["README documents install steps"],
        test_commands=["pytest tests/test_readme.py"],
        risk_level="medium",
        requires_human_approval=True,
        max_total_rounds=3,
        models=HocaRoleModelSelection(
            manager="manager-slot",
            worker="worker-slot",
            reviewer="reviewer-slot",
            fallback="fallback-slot",
        ),
        sandbox=HocaSandboxPolicy(enabled=True, network_mode="package-install"),
    )
    data = base.to_dict()
    data.update(overrides)
    return HocaTaskSpec.from_dict(data)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def test_build_worker_direct_prompt_contains_every_task_spec_binding() -> None:
    prompt = build_worker_direct_prompt(
        spec=sample_task_spec(),
        project_path=Path(f"{MAC_HOME}/project"),
        run_dir=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        round_number=2,
        task_spec_path=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
    )

    for expected in (
        "run_id: run-test",
        "repo_root_reference_only: /tmp/original",
        "base_branch: main",
        "task_branch: feat/direct",
        "issue_id: 42",
        "raw_request: Update README",
        "goal: Update README with install steps",
        "Do not edit source code",
        "README.md",
        "README documents install steps",
        "pytest tests/test_readme.py",
        "risk_level: medium",
        "requires_human_approval: true",
        "max_total_rounds: 3",
        "manager: manager-slot",
        "worker: worker-slot",
        "reviewer: reviewer-slot",
        "fallback: fallback-slot",
        "enabled: true",
        "network_mode: package-install",
    ):
        assert expected in prompt
    assert "Do not cd to repo_root_reference_only" in prompt
    assert "send every required argument in one call" in prompt
    assert "Verify repository diff after edits" in prompt
    assert "After one relevant validation command passes" in prompt
    assert "Do not continue exploring" in prompt
    assert "use temporary git repositories" in prompt
    assert "never run git init, git add, or git commit in any temp path" in prompt
    assert "never use `rm -rf`, `rm -Rf`, or other recursive cleanup" in prompt
    assert "do not recursively list the repository" in prompt
    assert "remove empty dirs with `rmdir`" in prompt
    assert "hermes" not in prompt.lower()


def test_build_worker_direct_prompt_adds_repo_clean_validation_rule() -> None:
    prompt = build_worker_direct_prompt(
        spec=sample_task_spec(
            raw_request="Add a repeatable repo-clean verification command",
            goal="Add a repeatable repo-clean verification command",
        ),
        project_path=Path(f"{MAC_HOME}/project"),
        run_dir=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        round_number=1,
        task_spec_path=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
    )

    assert "Repo-clean validation rule" in prompt
    assert "do not create a temp git repo or fake commit history" in prompt.lower()
    assert "unit test that mocks git output" in prompt
    assert "never use rm -rf" in prompt.lower()
    assert "HOCA-managed caches" in prompt
    assert "never delete or clean them" in prompt
    assert "git ls-files" in prompt


def test_build_worker_direct_prompt_injects_redacted_repair_brief() -> None:
    prompt = build_worker_direct_prompt(
        spec=sample_task_spec(),
        project_path=Path(f"{MAC_HOME}/project"),
        run_dir=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test"),
        round_number=2,
        task_spec_path=Path(f"{MAC_HOME}/project/.hoca-runtime/runs/run-test/task-spec.json"),
        repair_brief="Fix the README typo.\nDo not expose token=secret-value.",
    )

    assert "Repair brief for this attempt" in prompt
    assert "Fix the README typo" in prompt
    assert "secret-value" not in prompt
    assert "[redacted: possible secret]" in prompt


def test_write_worker_direct_prompt_writes_audit_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    path = write_worker_direct_prompt(
        spec=sample_task_spec(),
        project_path=tmp_path / "project",
        run_dir=run_dir,
        round_number=1,
        task_spec_path=run_dir / "task-spec.json",
    )

    assert path == run_dir / "prompts" / "worker-direct-prompt-1.txt"
    assert "Execute one bounded HOCA worker attempt directly in OpenHands" in path.read_text(
        encoding="utf-8"
    )


def test_run_worker_direct_invokes_openhands_without_hermes(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **kwargs):
        calls.append(tuple(command))
        (project / "README.md").write_text("changed\n", encoding="utf-8")
        (run_dir / "openhands-output.jsonl").write_text("{}\n", encoding="utf-8")
        (run_dir / "monitor-result.json").write_text(
            '{"stop_reason":"completed"}\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("hoca.worker_direct.subprocess.run", fake_run)

    result = run_worker_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.mode == "direct"
    assert result.exit_code == 0
    assert result.worker_attempt_path == worker_attempt_path(run_dir, 1)
    assert calls and calls[0][0].endswith("run-openhands-task.sh")
    assert all("hermes" not in part.lower() for part in calls[0])
    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report["mode"] == "direct"
    assert report["status"] == "completed"
    assert report["commands_run"] == ["run-openhands-task.sh"]
    assert (run_dir / "prompts" / "worker-direct-prompt-1.txt").is_file()


def test_run_worker_direct_monitor_stop_records_blocked(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")

    def fake_run(command, **kwargs):
        (run_dir / "openhands-output.jsonl").write_text("{}\n", encoding="utf-8")
        (run_dir / "monitor-result.json").write_text(
            '{"stop_reason":"secret_access"}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("hoca.worker_direct.subprocess.run", fake_run)

    result = run_worker_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["blocked_reason"] == "secret_access"


def test_run_worker_direct_tolerates_finalization_stall_after_diff(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "git":
            return real_run(command, **kwargs)
        (project / "README.md").write_text("changed\n", encoding="utf-8")
        (run_dir / "openhands-output.jsonl").write_text("{}\n", encoding="utf-8")
        (run_dir / "monitor-result.json").write_text('{"stop_reason":"stall"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="finalization stalled\n")

    monkeypatch.setattr("hoca.worker_direct.subprocess.run", fake_run)

    result = run_worker_direct(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )
    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert report["status"] == "completed"
    assert report["changed_files"] == ["README.md"]
