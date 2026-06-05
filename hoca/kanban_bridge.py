from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from hoca.fleet_contracts import HocaFleetTask, HocaLane


KANBAN_DISABLED_ENV = "HOCA_KANBAN_DISABLED"
HERMES_API_ENV = "HOCA_HERMES_API"


def _slugify_repo_name(path: Path) -> str:
    raw = path.name.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"


def board_name(project_path: Path) -> str:
    return f"hoca:{_slugify_repo_name(project_path)}"


def _run_hermes_command(*, command: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(command, check=False, text=True, capture_output=True)
    except OSError as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


def _extract_card_id(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    card_id = payload.get("id")
    if isinstance(card_id, str):
        value = card_id.strip()
        return value or None
    if isinstance(card_id, int):
        return str(card_id)
    return None


def _hermes_enabled() -> bool:
    from os import environ

    return environ.get(KANBAN_DISABLED_ENV, "").strip().lower() not in {"1", "true", "yes"}


def build_kanban_markers(
    *,
    lane_id: str | None = None,
    project_id: str | None = None,
    round_number: int | None = None,
    pr_url: str | None = None,
    decision: str | None = None,
    validation: str | None = None,
    escalation_reason: str | None = None,
    artifact_paths: tuple[str, ...] | None = None,
) -> dict[str, str]:
    payload = {
        "spec": f"lane_id={lane_id or '?'} project_id={project_id or '?'}",
        "round": f"{round_number}" if round_number is not None else "0",
        "artifact": ",".join(artifact_paths or []),
        "validation": validation or "",
        "decision": decision or "",
        "escalation": escalation_reason or "",
        "pr": pr_url or "",
    }
    return {key: value for key, value in payload.items() if value}


def map_task_to_kanban_payload(
    task: HocaFleetTask, *, board_name: str, workspace: str
) -> dict[str, str]:
    return {
        "board": board_name,
        "title": f"HOCA: {task.title or task.task_id}",
        "assignee": "hoca-manager",
        "workspace": workspace,
        "body": f"""HOCA Kanban Parent Task\n\ntask_id={task.task_id}\nproject_id={task.project_id}\nstatus={task.status}\nreadiness={task.readiness}\npriority={task.priority}""",
    }


def map_lane_to_kanban_comment(lane: HocaLane, *, markers: dict[str, str]) -> str:
    lines = [f"[spec] lane_id={lane.lane_id} task_id={lane.task_id} project={lane.project_id}"]
    for key, value in markers.items():
        lines.append(f"[{key}] {value}")
    return "\n".join(lines)


def create_parent_card(
    task: HocaFleetTask,
    project_path: Path,
    *,
    workspace: str = "runtime",
    dry_run: bool = False,
) -> str | None:
    if not _hermes_enabled():
        return None
    if dry_run:
        return "dry-run"

    payload = map_task_to_kanban_payload(
        task, board_name=board_name(project_path), workspace=workspace
    )
    return_code, stdout, stderr = _run_hermes_command(
        command=[
            "hermes",
            "kanban",
            "--board",
            payload["board"],
            "create",
            payload["title"],
            "--assignee",
            payload["assignee"],
            "--workspace",
            payload["workspace"],
            "--json",
        ]
    )
    if return_code != 0:
        return None
    try:
        parsed = json.loads(stdout)
        return _extract_card_id(parsed)
    except json.JSONDecodeError:
        return None


def sync_lane_to_kanban(
    lane: HocaLane,
    parent_card_id: str,
    *,
    board: str | None = None,
    markers: dict[str, str] | None = None,
) -> bool:
    if board is None:
        return False
    if not _hermes_enabled():
        return False

    message = map_lane_to_kanban_comment(lane, markers=markers or {"decision": "updated"})
    return_code, _, _ = _run_hermes_command(
        command=["hermes", "kanban", "--board", board, "comment", parent_card_id, message]
    )
    return return_code == 0


def _fetch_json(url: str, *, timeout: float = 3.0) -> dict[str, Any] | None:
    try:
        with urlopen(
            Request(url, headers={"Accept": "application/json"}), timeout=timeout
        ) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except (URLError, OSError, json.JSONDecodeError):
        return None


def _parse_kanban_list(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("items", "cards", "workers", "runs"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _read_worker_status_via_cli(*, lane_id: str, project_path: Path) -> dict[str, Any] | None:
    return_code, stdout, _ = _run_hermes_command(
        command=["hermes", "kanban", "--board", board_name(project_path), "list", "--json"]
    )
    if return_code != 0:
        return None

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    for item in _parse_kanban_list(payload):
        if item.get("lane_id") == lane_id or item.get("id") == lane_id:
            return item
    return None


def _read_run_detail_via_cli(*, run_id: str, project_path: Path) -> dict[str, Any] | None:
    return_code, stdout, _ = _run_hermes_command(
        command=["hermes", "kanban", "--board", board_name(project_path), "list", "--json"]
    )
    if return_code != 0:
        return None

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    for item in _parse_kanban_list(payload):
        if item.get("run_id") == run_id or item.get("id") == run_id:
            return item
    return None


def read_worker_status(*, lane_id: str, project_path: Path) -> dict[str, Any] | None:
    from os import environ

    base = environ.get(HERMES_API_ENV)
    if base:
        parsed = _fetch_json(
            f"{base.rstrip('/')}/workers?project={project_path.name}&lane={lane_id}"
        )
        if parsed is not None:
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    if item.get("lane_id") == lane_id or item.get("id") == lane_id:
                        return item

    return _read_worker_status_via_cli(lane_id=lane_id, project_path=project_path)


def read_run_detail(*, run_id: str, project_path: Path) -> dict[str, Any] | None:
    from os import environ

    base = environ.get(HERMES_API_ENV)
    if base:
        parsed = _fetch_json(f"{base.rstrip('/')}/runs/{run_id}")
        if isinstance(parsed, dict):
            return parsed

    return _read_run_detail_via_cli(run_id=run_id, project_path=project_path)
