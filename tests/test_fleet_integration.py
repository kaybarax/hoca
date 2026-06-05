from __future__ import annotations

import json
import subprocess
from pathlib import Path
from subprocess import CompletedProcess

from hoca.fleet_contracts import HocaFleetTask, HocaProject, HocaResourceBudget
from hoca.fleet_registry import FleetRegistry
from hoca.fleet_monitor import monitor_lane
from hoca.notifications import NotificationContext, notifications_from_snapshot
from hoca.resource_governor import ResourceGovernor
from hoca.scheduler import FleetScheduler


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HOCA Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _register_project(
    registry: FleetRegistry,
    *,
    project_id: str,
    repo_path: Path,
    max_parallel_tasks: int,
) -> None:
    registry.create_project(
        HocaProject(
            project_id=project_id,
            repo_path=str(repo_path),
            default_branch="main",
            max_parallel_tasks=max_parallel_tasks,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )


def test_fake_multi_project_scheduler_launches_both_projects(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    _init_repo(repo_a)
    _init_repo(repo_b)

    registry = FleetRegistry(control_root=tmp_path / "control")
    _register_project(registry, project_id="project-a", repo_path=repo_a, max_parallel_tasks=1)
    _register_project(registry, project_id="project-b", repo_path=repo_b, max_parallel_tasks=1)

    registry.create_task(
        HocaFleetTask(
            task_id="task-a-1",
            project_id="project-a",
            title="Task A1",
            status="queued",
            readiness="not_ready",
            priority=1,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-a-2",
            project_id="project-a",
            title="Task A2",
            status="queued",
            readiness="not_ready",
            priority=1,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-b-1",
            project_id="project-b",
            title="Task B1",
            status="queued",
            readiness="not_ready",
            priority=1,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )

    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=2,
        max_parallel_tasks=2,
        max_parallel_lanes=2,
        max_agents=10,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    scheduler = FleetScheduler(
        registry=registry,
        governor=ResourceGovernor(budget=budget),
        control_root=tmp_path / "control",
    )

    decisions = scheduler.tick()
    launches = [decision for decision in decisions if decision.decision_type == "launch"]

    assert len(launches) == 2
    assert {decision.project_id for decision in launches} == {"project-a", "project-b"}
    assert registry.get_task("task-a-1").status == "running"
    assert registry.get_task("task-b-1").status == "running"
    assert registry.get_task("task-a-2").status == "queued"
    lanes = registry.list_lanes()
    assert len(lanes) == 2
    assert all(lane.run_dir for lane in lanes)


def test_notifications_wait_until_ready_for_human(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    status_path = run_dir / "status.json"
    pr_url = "https://example.test/pr/1"
    context = NotificationContext(
        project_id="proj-1", task_id="task-1", task="Add retry logic", run_dir=run_dir
    )

    def fake_run_command_pending(
        command: list[str], cwd: Path | None = None
    ) -> CompletedProcess[str]:
        if command[:3] == ["gh", "pr", "checks"]:
            return CompletedProcess(
                command, 0, '[{"name":"ci","status":"pending","conclusion":null}]', ""
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("hoca.fleet_monitor._run_command", fake_run_command_pending)
    status_path.write_text(
        json.dumps({"status": "pr_created", "pr_url": pr_url, "base_ref": "main"}) + "\n",
        encoding="utf-8",
    )

    pending = monitor_lane("lane-1", run_dir)
    assert pending.state == "pr_created"
    assert notifications_from_snapshot(pending, context) == []

    def fake_run_command_pass(command: list[str], cwd: Path | None = None) -> CompletedProcess[str]:
        if command[:3] == ["gh", "pr", "checks"]:
            return CompletedProcess(
                command, 0, '[{"name":"ci","status":"completed","conclusion":"success"}]', ""
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("hoca.fleet_monitor._run_command", fake_run_command_pass)
    (run_dir / "openhands-review.txt").write_text("LGTM\n", encoding="utf-8")
    status_path.write_text(
        json.dumps({"status": "ready_for_human", "pr_url": pr_url, "base_ref": "main"}) + "\n",
        encoding="utf-8",
    )

    ready = monitor_lane("lane-1", run_dir)
    assert ready.state == "ready_for_human"

    first = notifications_from_snapshot(ready, context)
    assert len(first) == 1
    assert first[0].payload["action"] == "human_review_ready"
    assert notifications_from_snapshot(ready, context) == []
