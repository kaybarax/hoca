from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from hoca.agent_sessions import build_session, write_session
from hoca.cli import main
from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject
from hoca.fleet_reconcile import sync_registry_from_run_artifacts
from hoca.fleet_registry import FleetRegistry


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


def _seed_running_lane(tmp_path: Path) -> tuple[FleetRegistry, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(
        HocaProject(
            project_id="project-1",
            repo_path=str(repo),
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-1",
            project_id="project-1",
            status="running",
            readiness="ready",
            lane_ids=["lane-1"],
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    run_dir = repo / ".hoca-runtime" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    registry.create_lane(
        HocaLane(
            lane_id="lane-1",
            task_id="task-1",
            project_id="project-1",
            status="running",
            run_dir=str(run_dir),
            branch="feat/task-1",
            attempt_number=0,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    return registry, run_dir


def test_sync_registry_from_run_artifacts_marks_pr_created_lane_and_task(tmp_path: Path) -> None:
    registry, run_dir = _seed_running_lane(tmp_path)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "pr_created",
                "final_state": "pr_opened",
                "pr_url": "https://example.test/pull/1",
                "started_at": "2026-06-05T23:59:00Z",
                "ended_at": "2026-06-06T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    changed = sync_registry_from_run_artifacts(registry)

    assert "lane:lane-1:pr_created" in changed
    assert "task:task-1:completed" in changed
    lane = registry.get_lane("lane-1")
    task = registry.get_task("task-1")
    assert lane is not None
    assert lane.status == "pr_created"
    assert lane.started_at == "2026-06-05T23:59:00Z"
    assert lane.completed_at == "2026-06-06T00:00:00Z"
    assert lane.metadata["pr_url"] == "https://example.test/pull/1"
    assert task is not None
    assert task.status == "completed"
    assert task.readiness == "draft_ready"
    assert task.metadata["pr_url"] == "https://example.test/pull/1"


def test_sync_registry_uses_latest_project_run_when_lane_points_at_adapter_dir(
    tmp_path: Path, monkeypatch
) -> None:
    registry, _ = _seed_running_lane(tmp_path)
    lane = registry.get_lane("lane-1")
    assert lane is not None
    repo = tmp_path / "repo"
    adapter_dir = repo / ".hoca-runtime" / "fleet-lanes" / "lane-1"
    adapter_dir.mkdir(parents=True)
    run_dir = repo / ".hoca-runtime" / "runs" / "run-20260606T000000Z-lane-1"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "pr_created",
                "final_state": "pr_opened",
                "pr_url": "https://example.test/pull/adapter",
                "ended_at": "2026-06-06T00:04:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    registry.update_lane(
        "lane-1",
        HocaLane(
            **{
                **lane.to_dict(),
                "run_dir": str(adapter_dir),
                "session_id": "session-1",
            }
        ),
    )
    session = build_session(
        session_id="session-1",
        lane_id="lane-1",
        adapter_id="openhands-hermes",
        started_at="2026-06-06T00:00:00Z",
    )
    write_session(
        registry.paths.root,
        type(session)(**{**session.to_dict(), "process_id": 12345}),
    )
    monkeypatch.setattr("hoca.fleet_reconcile._process_alive", lambda pid: False)

    changed = sync_registry_from_run_artifacts(registry)

    assert "lane:lane-1:pr_created" in changed
    assert "task:task-1:completed" in changed
    synced_lane = registry.get_lane("lane-1")
    assert synced_lane is not None
    assert synced_lane.status == "pr_created"
    assert synced_lane.metadata["pr_url"] == "https://example.test/pull/adapter"


def test_lane_and_task_list_sync_before_display(tmp_path: Path) -> None:
    _, run_dir = _seed_running_lane(tmp_path)
    (run_dir / "final-state.json").write_text(
        json.dumps(
            {
                "status": "pr_opened",
                "pr_url": "https://example.test/pull/2",
                "completed_at": "2026-06-06T00:01:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = {"HOCA_CONTROL_ROOT": str(tmp_path / "control")}

    lane_result = CliRunner().invoke(main, ["lane", "list"], env=env)
    task_result = CliRunner().invoke(main, ["task", "list"], env=env)
    fleet_result = CliRunner().invoke(main, ["fleet", "status"], env=env)

    assert lane_result.exit_code == 0
    assert "lane-1\ttask-1\tproject-1\tpr_created" in lane_result.output
    assert task_result.exit_code == 0
    assert "task-1\tproject-1\tcompleted\tdraft_ready" in task_result.output
    assert fleet_result.exit_code == 0
    assert "Ready PRs: 1" in fleet_result.output


def test_fleet_reconcile_command_reports_updates(tmp_path: Path) -> None:
    registry, run_dir = _seed_running_lane(tmp_path)
    (run_dir / "status.json").write_text(
        json.dumps({"status": "blocked", "ended_at": "2026-06-06T00:02:00Z"}) + "\n",
        encoding="utf-8",
    )
    env = {"HOCA_CONTROL_ROOT": str(tmp_path / "control")}

    result = CliRunner().invoke(main, ["fleet", "reconcile"], env=env)

    assert result.exit_code == 0
    assert "Reconciled lane:lane-1:blocked" in result.output
    assert "Reconciled task:task-1:blocked" in result.output
    assert registry.get_lane("lane-1").status == "blocked"
    assert registry.get_task("task-1").status == "blocked"


def test_fleet_reconcile_command_reports_noop(tmp_path: Path) -> None:
    _seed_running_lane(tmp_path)
    env = {"HOCA_CONTROL_ROOT": str(tmp_path / "control")}

    result = CliRunner().invoke(main, ["fleet", "reconcile"], env=env)

    assert result.exit_code == 0
    assert "Fleet registry already reconciled." in result.output


def test_sync_registry_blocks_stale_running_lane_with_dead_session(
    tmp_path: Path, monkeypatch
) -> None:
    registry, _ = _seed_running_lane(tmp_path)
    lane = registry.get_lane("lane-1")
    assert lane is not None
    registry.update_lane(
        "lane-1",
        HocaLane(
            **{
                **lane.to_dict(),
                "session_id": "session-1",
                "run_dir": str(tmp_path / "repo" / ".hoca-runtime" / "runs" / "run-missing"),
            }
        ),
    )
    session = build_session(
        session_id="session-1",
        lane_id="lane-1",
        adapter_id="openhands-hermes",
        started_at="2026-06-06T00:00:00Z",
    )
    write_session(
        registry.paths.root,
        type(session)(
            **{
                **session.to_dict(),
                "process_id": 12345,
                "ended_at": "2026-06-06T00:03:00Z",
            }
        ),
    )
    monkeypatch.setattr("hoca.fleet_reconcile._process_alive", lambda pid: False)

    changed = sync_registry_from_run_artifacts(registry)

    assert "lane:lane-1:blocked" in changed
    assert "task:task-1:blocked" in changed
    stale_lane = registry.get_lane("lane-1")
    assert stale_lane is not None
    assert stale_lane.status == "blocked"
    assert stale_lane.metadata["status_reason"] == "adapter process exited before final run artifact"
    stale_task = registry.get_task("task-1")
    assert stale_task is not None
    assert stale_task.status == "blocked"
