from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.fleet_contracts import HocaNotification
from hoca.fleet_monitor import LaneMonitorSnapshot
from hoca.run_state import write_json_atomic


def _now_iso() -> str:
    from time import gmtime, strftime

    return strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())


@dataclass(frozen=True)
class NotificationContext:
    project_id: str | None
    task_id: str | None
    task: str | None
    run_dir: Path


def notification_state_path(run_dir: Path) -> Path:
    return run_dir / ".fleet-monitor-notify-state.json"


def _read_notification_state(run_dir: Path) -> dict[str, str]:
    raw = notification_state_path(run_dir)
    if not raw.is_file():
        return {}
    try:
        payload = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        return {str(key): str(value) for key, value in payload.items() if isinstance(value, str)}
    return {}


def _write_notification_state(run_dir: Path, state: dict[str, str]) -> None:
    write_json_atomic(notification_state_path(run_dir), state)


def _dedupe_key(lane_id: str, action: str, state: str) -> str:
    return f"{lane_id}:{action}:{state}"


def _action_for_snapshot(snapshot: LaneMonitorSnapshot, *, resource_block_reason: str | None = None) -> str | None:
    if resource_block_reason:
        return "resource_exhaustion"
    if snapshot.state == "ready_for_human":
        return "human_review_ready"
    if snapshot.state == "blocked":
        if (snapshot.status_reason or "").lower() in {"monitor", "monitor_stop", "monitor_timeout", "secret_access"}:
            return "safety_monitor_block"
        return "lane_blocked"
    if snapshot.state == "completed" and snapshot.status == "stalled":
        return "lane_stalled"
    return None


def _build_payload(
    *,
    lane_id: str,
    action: str,
    snapshot: LaneMonitorSnapshot,
    context: NotificationContext,
) -> HocaNotification:
    return HocaNotification(
        notification_id=f"notify-{lane_id}-{action}-{_now_iso()}",
        lane_id=lane_id,
        channel="local",
        recipient="operator",
        message=(
            f"Lane {lane_id} is now '{action}'. "
            f"status={snapshot.status or 'unknown'} pr_check={snapshot.pr_check or 'unknown'}"
        ),
        status="queued",
        created_at=_now_iso(),
        payload={
            "project_id": context.project_id or "",
            "task_id": context.task_id or "",
            "task": context.task or "",
            "lane_id": lane_id,
            "status": snapshot.status or "",
            "action": action,
            "report_path": str(context.run_dir),
            "pr_url": snapshot.pr_url or "",
        },
    )


def notifications_from_snapshot(
    snapshot: LaneMonitorSnapshot,
    context: NotificationContext,
    *,
    resource_block_reason: str | None = None,
) -> list[HocaNotification]:
    action = _action_for_snapshot(snapshot, resource_block_reason=resource_block_reason)
    if action is None:
        return []

    state = _read_notification_state(context.run_dir)
    cache_key = _dedupe_key(snapshot.lane_id, action, snapshot.state)
    if state.get("last_key") == cache_key:
        return []

    notification = _build_payload(
        lane_id=snapshot.lane_id,
        action=action,
        snapshot=snapshot,
        context=context,
    )
    _write_notification_state(
        context.run_dir,
        {
            "last_key": cache_key,
            "lane_id": snapshot.lane_id,
            "action": action,
            "state": snapshot.state,
            "updated_at": _now_iso(),
        },
    )
    return [notification]
