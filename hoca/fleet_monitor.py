from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.run_state import read_optional_json
from hoca.kanban_bridge import read_worker_status

MONITOR_RUNNING_STATES = {"running", "review", "validation", "starting", "cleanup"}
MONITOR_READY_STATES = {
    "committed",
    "staged",
    "ready_for_human",
    "needs_human_staging",
    "ready",
}
MONITOR_FAILED_STATES = {
    "failed",
    "blocked",
    "review_failed",
    "ci_failed",
    "stopped",
    "timeout",
}
MONITOR_COMPLETED_STATES = {
    "completed",
    "ready_for_human",
}


@dataclass(frozen=True)
class LaneMonitorSnapshot:
    lane_id: str
    state: str
    status: str | None
    status_reason: str | None
    pr_url: str | None
    has_validation_artifacts: bool
    has_review_artifacts: bool
    terminal_alive: bool
    should_process: bool
    run_dir: str
    hermes_worker: dict[str, Any] | None = None
    git_changed_files: int | None = None
    git_diff_files: int | None = None
    git_merge_base_ok: bool | None = None
    pr_check: str | None = None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_file_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _status_payload(run_dir: Path) -> dict[str, Any]:
    status_path = run_dir / "status.json"
    raw = read_optional_json(status_path)
    if isinstance(raw, dict):
        return raw
    return {}


def _resolve_project_path(payload: dict[str, Any], project_path: Path | None = None) -> Path | None:
    if project_path is not None:
        resolved = project_path.expanduser().resolve()
        if resolved.is_dir():
            return resolved

    for key in ("project_path", "repo_path", "repo_root", "worktree_path"):
        raw = payload.get(key)
        if not isinstance(raw, str):
            continue
        candidate = Path(raw).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    return None


def _read_active_hermes_worker_status(
    lane_id: str,
    *,
    project_path: Path | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not project_path:
        return None
    resolved = _resolve_project_path(payload, project_path=project_path)
    if resolved is None:
        return None
    return read_worker_status(lane_id=lane_id, project_path=resolved)


def _snapshot_keys_for_artifacts(run_dir: Path) -> dict[str, bool]:
    validation_matches = any(
        run_dir.glob(pattern) for pattern in ("validation-report-*.json", "tests-summary.md")
    )
    review_matches = any(
        run_dir.glob(pattern)
        for pattern in (
            "openhands-review.txt",
            "review-report-*.json",
            "reviews/review-report-*.json",
        )
    )
    return {
        "has_validation_report": validation_matches,
        "has_review_artifacts": review_matches,
        "has_status": (run_dir / "status.json").is_file(),
        "has_monitor_result": (run_dir / "monitor-result.json").is_file(),
    }


def _infer_git_root(run_dir: Path) -> Path | None:
    current = run_dir
    for _ in range(6):
        if (current / ".git").is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _run_command(
    command: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _read_git_status_summary(run_dir: Path) -> int | None:
    root = _infer_git_root(run_dir)
    if root is None:
        return None

    status_cmd = _run_command(["git", "status", "--short"], cwd=root)
    if status_cmd is None:
        return None

    return len([line for line in status_cmd.stdout.splitlines() if line.strip()])


def _read_git_diff_summary(root: Path, status: dict[str, Any]) -> int | None:
    candidates = [
        value
        for value in [status.get("base_ref"), status.get("base_branch")]
        if isinstance(value, str)
    ]
    if not candidates:
        return None

    command = ["git", "diff", "--name-only", f"{candidates[0]}...HEAD"]
    result = _run_command(command, cwd=root)
    if result is None or result.returncode != 0:
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _check_merge_base(root: Path, status: dict[str, Any]) -> bool | None:
    candidates = [
        value
        for value in [status.get("base_ref"), status.get("base_branch")]
        if isinstance(value, str)
    ]
    if not candidates:
        return None

    command = ["git", "merge-base", "--is-ancestor", candidates[0], "HEAD"]
    result = _run_command(command, cwd=root)
    if result is None:
        return None
    return result.returncode == 0


def _pr_check(pr_url: str | None) -> str | None:
    if not pr_url:
        return None

    pr_command = ["gh", "pr", "checks", pr_url, "--json", "conclusion,status,name"]
    result = _run_command(pr_command)
    if result is None or result.returncode != 0:
        return "unknown"

    try:
        checks = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return "unknown"

    if not isinstance(checks, list) or not checks:
        return "unknown"

    conclusions = [str(item.get("conclusion", "")) for item in checks if isinstance(item, dict)]
    statuses = [str(item.get("status", "")) for item in checks if isinstance(item, dict)]

    if any(status in {"in_progress", "queued", "pending"} for status in statuses):
        return "running"
    if any(
        conclusion in {"failure", "cancelled", "timed_out", "action_required"}
        for conclusion in conclusions
    ):
        return "failed"
    if all(conclusion in {"success", "neutral", "skipped"} for conclusion in conclusions):
        return "passed"
    return "unknown"


def classify_lane_state(
    payload: dict[str, Any],
    *,
    terminal_alive: bool,
    pr_check: str | None = None,
) -> str:
    status = payload.get("status")
    reason = payload.get("reason")
    if not status:
        if terminal_alive:
            return "running"
        return "missing_artifacts"

    status_text = str(status)
    if status_text in MONITOR_RUNNING_STATES:
        return "running"
    if status_text in MONITOR_FAILED_STATES:
        return "blocked"
    if status_text in MONITOR_READY_STATES:
        if status_text == "ready_for_human":
            return "ready_for_human"
        if reason:
            sanitized = str(reason).lower()
            if "monitor" in sanitized or "timeout" in sanitized:
                return "blocked"
        if terminal_alive:
            return "ready_for_human"
        return "completed"
    if status_text in MONITOR_COMPLETED_STATES:
        return "completed"

    if pr_check == "failed":
        return "blocked"
    if not terminal_alive and status_text in {"started", "", "unknown"}:
        return "stalled"
    return str(status_text)


def _state_cache_path(run_dir: Path) -> Path:
    return run_dir / ".fleet-monitor-state.json"


def _load_last_state(run_dir: Path) -> dict[str, str]:
    cache = read_optional_json(_state_cache_path(run_dir))
    if isinstance(cache, dict):
        return {str(key): str(value) for key, value in cache.items()}
    return {}


def _save_state(run_dir: Path, *, state: str, status: str | None, pr_check: str | None) -> None:
    from hoca.run_state import write_json_atomic

    write_json_atomic(
        _state_cache_path(run_dir),
        {
            "state": state,
            "status": status or "",
            "pr_check": pr_check or "",
            "updated_at": _now_iso(),
        },
    )


def monitor_lane(
    lane_id: str,
    run_dir: Path,
    *,
    terminal_alive: bool | None = None,
    pr_url_override: str | None = None,
    project_path: Path | None = None,
) -> LaneMonitorSnapshot:
    run_dir = run_dir.expanduser().resolve()
    payload = _status_payload(run_dir)
    terminal = terminal_alive if terminal_alive is not None else False

    status = payload.get("status")
    if isinstance(status, str):
        status = status.strip() or None

    status_reason = payload.get("reason")
    if not isinstance(status_reason, str):
        status_reason = None

    keys = _snapshot_keys_for_artifacts(run_dir)
    changed_files_count: int | None = None
    diff_files_count: int | None = None
    merge_base_ok: bool | None = None
    if keys["has_status"]:
        changed_files_count = _read_git_status_summary(run_dir)
        root = _infer_git_root(run_dir)
        if root is not None:
            merge_base_ok = _check_merge_base(root, payload)
            diff_files_count = _read_git_diff_summary(root, payload)

    pr_url = None
    if "pr_url" in payload and isinstance(payload["pr_url"], str):
        pr_url = payload["pr_url"] or None
    if not pr_url:
        pr_url = pr_url_override

    pr_check = _pr_check(pr_url)

    state = classify_lane_state(payload, terminal_alive=terminal, pr_check=pr_check)

    hermes_worker = _read_active_hermes_worker_status(
        lane_id,
        payload=payload,
        project_path=project_path,
    )

    last = _load_last_state(run_dir)
    should_process = last.get("state") != state or not last
    if should_process:
        _save_state(run_dir, state=state, status=status, pr_check=pr_check)

    if state in {"completed", "ready_for_human", "blocked", "missing_artifacts"} and not terminal:
        if not should_process:
            state = f"{state}:stabilized"

    return LaneMonitorSnapshot(
        lane_id=lane_id,
        state=state,
        status=status,
        status_reason=status_reason,
        pr_url=pr_url,
        has_validation_artifacts=keys["has_validation_report"],
        has_review_artifacts=keys["has_review_artifacts"],
        terminal_alive=terminal,
        hermes_worker=hermes_worker,
        should_process=should_process,
        run_dir=str(run_dir),
        git_changed_files=changed_files_count,
        git_diff_files=diff_files_count,
        git_merge_base_ok=merge_base_ok,
        pr_check=pr_check,
    )


def missing_artifact_reason(run_dir: Path) -> str:
    payload = _status_payload(run_dir)
    if not payload:
        return "status.json is missing"
    status = payload.get("status")
    if status in ("failed", "blocked"):
        reason = payload.get("reason")
        return str(reason) if isinstance(reason, str) and reason else "blocked"
    return ""
