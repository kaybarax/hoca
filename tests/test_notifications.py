from __future__ import annotations

from pathlib import Path

from hoca.fleet_monitor import LaneMonitorSnapshot
from hoca.notifications import (
    NotificationContext,
    notifications_from_snapshot,
    notification_state_path,
)


def test_notifications_for_ready_human_are_payloadd_and_deduped(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = LaneMonitorSnapshot(
        lane_id="lane-1",
        state="ready_for_human",
        status="needs_human_staging",
        status_reason=None,
        pr_url="https://example.test/pr/1",
        has_validation_artifacts=True,
        has_review_artifacts=True,
        terminal_alive=True,
        should_process=True,
        run_dir=str(run_dir),
    )

    context = NotificationContext(
        project_id="proj-1",
        task_id="task-1",
        task="Add retry logic",
        run_dir=run_dir,
    )

    first = notifications_from_snapshot(snapshot, context)
    assert len(first) == 1
    assert first[0].payload["action"] == "human_review_ready"
    assert first[0].payload["lane_id"] == "lane-1"

    second = notifications_from_snapshot(snapshot, context)
    assert second == []

    state = notification_state_path(run_dir).read_text(encoding="utf-8")
    assert "human_review_ready" in state


def test_resource_exhaustion_notification_is_emitted(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = LaneMonitorSnapshot(
        lane_id="lane-2",
        state="blocked",
        status="blocked",
        status_reason="resource cap reached",
        pr_url=None,
        has_validation_artifacts=False,
        has_review_artifacts=False,
        terminal_alive=False,
        should_process=True,
        run_dir=str(run_dir),
    )
    context = NotificationContext(
        project_id="proj-2",
        task_id="task-2",
        task="Build worker",
        run_dir=run_dir,
    )

    notifications = notifications_from_snapshot(
        snapshot,
        context,
        resource_block_reason="fleet capacity reached",
    )
    assert len(notifications) == 1
    assert notifications[0].payload["action"] == "resource_exhaustion"


def test_notifications_for_human_blocked_lane_are_emitted(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = LaneMonitorSnapshot(
        lane_id="lane-3",
        state="blocked",
        status="blocked",
        status_reason="human clarification needed",
        pr_url=None,
        has_validation_artifacts=False,
        has_review_artifacts=False,
        terminal_alive=False,
        should_process=True,
        run_dir=str(run_dir),
    )

    context = NotificationContext(
        project_id="proj-3",
        task_id="task-3",
        task="Resolve edge cases",
        run_dir=run_dir,
    )

    notifications = notifications_from_snapshot(snapshot, context)
    assert len(notifications) == 1
    assert notifications[0].payload["action"] == "lane_blocked"


def test_safety_monitor_blocking_notification_is_emitted(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = LaneMonitorSnapshot(
        lane_id="lane-4",
        state="blocked",
        status="blocked",
        status_reason="monitor stop: suspicious command output",
        pr_url=None,
        has_validation_artifacts=False,
        has_review_artifacts=False,
        terminal_alive=False,
        should_process=True,
        run_dir=str(run_dir),
    )

    context = NotificationContext(
        project_id="proj-4",
        task_id="task-4",
        task="Harden worker",
        run_dir=run_dir,
    )

    notifications = notifications_from_snapshot(snapshot, context)
    assert len(notifications) == 1
    assert notifications[0].payload["action"] == "safety_monitor_block"
