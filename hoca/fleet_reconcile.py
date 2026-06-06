from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from hoca.fleet_contracts import HocaFleetTask, HocaLane
from hoca.fleet_registry import FleetRegistry
from hoca.run_state import read_optional_json

FINAL_RUN_STATUSES = frozenset({"pr_created", "ready_for_human", "blocked", "failed"})
FINAL_STATE_TO_LANE_STATUS = {
    "pr_opened": "pr_created",
    "ready_for_human": "ready_for_human",
    "blocked": "blocked",
    "failed": "failed",
}
LANE_TO_TASK_STATUS = {
    "pr_created": "completed",
    "ready_for_human": "completed",
    "blocked": "blocked",
    "failed": "blocked",
}
LANE_TO_TASK_READINESS = {
    "pr_created": "draft_ready",
    "ready_for_human": "ready",
    "blocked": "blocked",
    "failed": "blocked",
}


def _run_dir_for_lane(registry: FleetRegistry, lane: HocaLane) -> Path | None:
    if not lane.run_dir:
        return None
    raw = Path(lane.run_dir)
    if raw.is_absolute():
        return raw
    project = registry.get_project(lane.project_id)
    if project is None:
        return None
    return Path(project.repo_path) / raw


def _lane_status_from_artifacts(run_dir: Path) -> tuple[str | None, dict[str, Any]]:
    final_state = read_optional_json(run_dir / "final-state.json")
    if isinstance(final_state, dict):
        final_status = str(final_state.get("status") or "")
        mapped = FINAL_STATE_TO_LANE_STATUS.get(final_status)
        if mapped:
            return mapped, final_state

    status = read_optional_json(run_dir / "status.json")
    if isinstance(status, dict):
        raw_status = str(status.get("status") or "")
        if raw_status in FINAL_RUN_STATUSES:
            return raw_status, status
    return None, {}


def sync_lane_from_artifacts(registry: FleetRegistry, lane: HocaLane) -> HocaLane | None:
    run_dir = _run_dir_for_lane(registry, lane)
    if run_dir is None:
        return None
    next_status, artifact = _lane_status_from_artifacts(run_dir)
    if next_status is None:
        return None

    completed_at = str(artifact.get("completed_at") or artifact.get("ended_at") or "").strip()
    metadata = dict(lane.metadata or {})
    pr_url = artifact.get("pr_url")
    final_state = artifact.get("final_state") or artifact.get("status")
    if pr_url:
        metadata["pr_url"] = str(pr_url)
    if final_state:
        metadata["final_state"] = str(final_state)

    next_lane = replace(
        lane,
        status=next_status,  # type: ignore[arg-type]
        completed_at=completed_at or lane.completed_at,
        updated_at=completed_at or lane.updated_at,
        metadata=metadata,
    )
    if next_lane != lane:
        registry.update_lane(lane.lane_id, next_lane)
        return next_lane
    return None


def sync_task_from_lanes(registry: FleetRegistry, task: HocaFleetTask) -> HocaFleetTask | None:
    lanes = registry.list_lanes(task_id=task.task_id)
    final_lanes = [lane for lane in lanes if lane.status in LANE_TO_TASK_STATUS]
    if not final_lanes:
        return None
    # Prefer ready/completed PR states over failures if any successful lane exists.
    final_lanes.sort(key=lambda lane: lane.completed_at or lane.updated_at or lane.created_at)
    successful = [lane for lane in final_lanes if lane.status in {"pr_created", "ready_for_human"}]
    source = successful[-1] if successful else final_lanes[-1]
    task_status = LANE_TO_TASK_STATUS[source.status]
    readiness = LANE_TO_TASK_READINESS[source.status]
    completed_at = source.completed_at or task.completed_at
    metadata = dict(task.metadata or {})
    if source.metadata:
        for key in ("pr_url", "final_state"):
            if key in source.metadata:
                metadata[key] = source.metadata[key]
    metadata["final_lane_status"] = source.status

    next_task = replace(
        task,
        status=task_status,  # type: ignore[arg-type]
        readiness=readiness,  # type: ignore[arg-type]
        completed_at=completed_at,
        updated_at=completed_at or task.updated_at,
        metadata=metadata,
    )
    if next_task != task:
        registry.update_task(task.task_id, next_task)
        return next_task
    return None


def sync_registry_from_run_artifacts(registry: FleetRegistry) -> list[str]:
    changed: list[str] = []
    for lane in registry.list_lanes():
        next_lane = sync_lane_from_artifacts(registry, lane)
        if next_lane is not None:
            changed.append(f"lane:{next_lane.lane_id}:{next_lane.status}")

    for task in registry.list_tasks():
        next_task = sync_task_from_lanes(registry, task)
        if next_task is not None:
            changed.append(f"task:{next_task.task_id}:{next_task.status}")
    return changed
