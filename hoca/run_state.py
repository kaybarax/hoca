from __future__ import annotations

import atexit
import json
import os
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hoca.config import HocaConfig, load_config
from hoca.run_layout import (
    ensure_run_layout,
    final_state_path,
    manager_decision_path,
    review_report_path,
    sandbox_policy_path,
    status_path,
    task_spec_path,
    validation_report_path,
    worker_attempt_path,
)

WORKFLOW_VERSION = 2

ReportKind = Literal[
    "status",
    "task_spec",
    "sandbox_policy",
    "final_state",
    "worker_attempt",
    "review_report",
    "manager_decision",
    "validation_report",
]

_ROUND_REPORT_KINDS = frozenset(
    {"worker_attempt", "review_report", "manager_decision", "validation_report"}
)

RUN_STATE_DIRNAME = ".hoca-runtime"

_held_locks: list[Path] = []
_held_lock_ids: dict[Path, tuple[int, int]] = {}


def now_epoch() -> int:
    return int(time.time())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{now_epoch()}"


def ensure_run_dir(project_path: Path, run_id: str) -> Path:
    run_dir = project_path / RUN_STATE_DIRNAME / "runs" / run_id
    ensure_run_layout(run_dir)
    return run_dir


def create_run_layout(project_path: Path, run_id: str) -> Path:
    """Create the standard run directory layout for a new run."""
    return ensure_run_dir(project_path, run_id)


def ensure_runtime_dirs(project_path: Path) -> Path:
    runtime = project_path / RUN_STATE_DIRNAME
    (runtime / "runs").mkdir(parents=True, exist_ok=True)
    (runtime / "logs").mkdir(parents=True, exist_ok=True)
    return runtime


def ensure_gitignore(project_path: Path) -> bool:
    gitignore = project_path / ".gitignore"
    rule = RUN_STATE_DIRNAME + "/"
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if rule in lines:
            return False
    with gitignore.open("a", encoding="utf-8") as f:
        f.write(rule + "\n")
    return True


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    write_json(temp_path, data)
    temp_path.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (json.JSONDecodeError, OSError):
        return None


def current_round(run_dir: Path, *, prefix: str, subdir: str) -> int:
    artifact_dir = run_dir / subdir
    if not artifact_dir.is_dir():
        return 0
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.json$")
    rounds = [
        int(match.group(1))
        for path in artifact_dir.iterdir()
        if path.is_file() and (match := pattern.match(path.name))
    ]
    return max(rounds, default=0)


def optional_report_path(
    run_dir: Path,
    kind: ReportKind,
    *,
    round_number: int | None = None,
) -> Path:
    if kind == "status":
        return status_path(run_dir)
    if kind == "task_spec":
        return task_spec_path(run_dir)
    if kind == "sandbox_policy":
        return sandbox_policy_path(run_dir)
    if kind == "final_state":
        return final_state_path(run_dir)
    if kind not in _ROUND_REPORT_KINDS:
        raise ValueError(f"Unknown report kind: {kind}")
    if round_number is None:
        raise ValueError(f"{kind} requires round_number")
    if kind == "worker_attempt":
        return worker_attempt_path(run_dir, round_number)
    if kind == "review_report":
        return review_report_path(run_dir, round_number)
    if kind == "manager_decision":
        return manager_decision_path(run_dir, round_number)
    return validation_report_path(run_dir, round_number)


def read_optional_report(
    run_dir: Path,
    kind: ReportKind,
    *,
    round_number: int | None = None,
) -> dict[str, Any] | None:
    """Read a structured report from the run directory when present."""
    return read_optional_json(optional_report_path(run_dir, kind, round_number=round_number))


def current_run_round(run_dir: Path) -> int:
    """Return the highest round number present across structured artifacts."""
    return max(
        current_round(run_dir, prefix="worker-attempt-", subdir="attempts"),
        current_round(run_dir, prefix="review-report-", subdir="reviews"),
        current_round(run_dir, prefix="manager-decision-", subdir="decisions"),
        current_round(run_dir, prefix="validation-report-", subdir="validation"),
    )


def write_final_state(run_dir: Path, state: dict[str, Any]) -> Path:
    """Write ``final-state.json`` atomically."""
    ensure_run_layout(run_dir)
    path = final_state_path(run_dir)
    write_json_atomic(path, state)
    return path


def _read_text_artifact(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _read_line_artifact(path: Path) -> list[str]:
    text = _read_text_artifact(path)
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def summarize_run_for_pr_body(
    run_dir: Path,
    *,
    task: str,
    issue_id: str | None = None,
) -> dict[str, str]:
    """Build PR body section fragments keyed by template slug."""
    task_oneline = " ".join(task.split())
    fragments: dict[str, str] = {"summary": task_oneline}

    changed_files = _read_line_artifact(run_dir / "changed-files.txt")
    if not changed_files:
        final_state = read_optional_report(run_dir, "final_state")
        if final_state:
            changed_files = [str(path) for path in final_state.get("changed_files", []) if path]

    changes_parts: list[str] = []
    if changed_files:
        changes_parts.append("Changed files:")
        changes_parts.extend(f"- {path}" for path in changed_files)
    commit_log = _read_text_artifact(run_dir / "commit-log.txt")
    if commit_log:
        if changes_parts:
            changes_parts.append("")
        changes_parts.extend(["```text", commit_log, "```"])
    fragments["changes"] = (
        "\n".join(changes_parts)
        if changes_parts
        else "_No change list recorded in run artifacts._"
    )

    validation_text = _read_text_artifact(run_dir / "tests-summary.md")
    if not validation_text:
        validation_round = current_round(
            run_dir, prefix="validation-report-", subdir="validation"
        )
        if validation_round:
            report = read_optional_report(
                run_dir, "validation_report", round_number=validation_round
            )
            if report:
                lines = [f"- **Tests passed**: {report.get('tests_passed')}"]
                blockers = report.get("hard_blockers") or []
                if blockers:
                    lines.append(f"- **Hard blockers**: {', '.join(blockers)}")
                validation_text = "\n".join(lines)
    fragments["validation"] = (
        validation_text or "_No `tests-summary.md` found in the run directory._"
    )

    review_text = _read_text_artifact(run_dir / "openhands-review.txt")
    if not review_text:
        review_round = current_round(run_dir, prefix="review-report-", subdir="reviews")
        if review_round:
            report = read_optional_report(
                run_dir, "review_report", round_number=review_round
            )
            if report:
                verdict = report.get("verdict", "unknown")
                pr_notes = report.get("pr_notes") or {}
                notes = pr_notes.get("summary") or []
                review_lines = [f"**Verdict**: {verdict}"]
                review_lines.extend(f"- {note}" for note in notes)
                review_text = "\n".join(review_lines)

    if review_text:
        if "LGTM" in review_text.upper():
            fragments["code-review"] = (
                "**Status**: LGTM present in code review output.\n\n"
                "Full review output is saved in the HOCA run artifacts."
            )
        else:
            fragments["code-review"] = (
                "**Status**: LGTM not detected in code review output "
                "(human review recommended).\n\n"
                "Full review output is saved in the HOCA run artifacts."
            )
    else:
        fragments["code-review"] = (
            "_No `openhands-review.txt` found in the run directory._"
        )

    fragments["risk"] = _read_text_artifact(run_dir / "risk-notes.txt") or (
        "None noted in run metadata."
    )
    fragments["linked-issue"] = f"Issue #{issue_id}" if issue_id else "None"
    return fragments


def list_round_artifact_paths(run_dir: Path, subdir: str, prefix: str) -> list[str]:
    artifact_dir = run_dir / subdir
    if not artifact_dir.is_dir():
        return []
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.json$")
    paths = [
        str(path)
        for path in sorted(
            artifact_dir.iterdir(),
            key=lambda item: int(pattern.match(item.name).group(1))
            if pattern.match(item.name)
            else 0,
        )
        if path.is_file() and pattern.match(path.name)
    ]
    return paths


def workflow_fields_from_config(cfg: HocaConfig | None = None) -> dict[str, Any]:
    """Return workflow metadata fields stored on ``status.json``."""
    cfg = cfg or load_config()
    return {
        "workflow_version": WORKFLOW_VERSION,
        "use_hermes_profiles": cfg.use_hermes_profiles,
        "structured_reports": cfg.use_structured_reports,
        "max_total_rounds": cfg.max_total_rounds,
        "sandbox_mode": "docker" if cfg.use_sandbox else "host",
    }


def sandbox_mode_for_run(run_dir: Path, *, cfg: HocaConfig | None = None) -> str:
    policy = read_optional_report(run_dir, "sandbox_policy")
    if policy is not None:
        return "docker" if bool(policy.get("enabled", True)) else "host"
    cfg = cfg or load_config()
    return "docker" if cfg.use_sandbox else "host"


def derived_status_fields(run_dir: Path) -> dict[str, Any]:
    """Compute artifact-backed status fields from the run directory."""
    pr_url = None
    pr_url_path = run_dir / "pr-url.txt"
    if pr_url_path.is_file():
        pr_url = pr_url_path.read_text(encoding="utf-8").strip() or None

    final_state = None
    final_report = read_optional_report(run_dir, "final_state")
    if final_report:
        final_state = final_report.get("status")

    return {
        "current_round": current_run_round(run_dir),
        "final_state": final_state,
        "pr_url": pr_url,
        "sandbox_mode": sandbox_mode_for_run(run_dir),
    }


def merge_status_snapshot(
    run_dir: Path,
    updates: dict[str, Any],
    *,
    include_workflow_fields: bool = False,
    cfg: HocaConfig | None = None,
) -> dict[str, Any]:
    path = status_path(run_dir)
    data = read_optional_json(path) or {}
    data.update(updates)
    if include_workflow_fields:
        data.update(workflow_fields_from_config(cfg))
    data.update(derived_status_fields(run_dir))
    return data


def write_initial_status(
    run_dir: Path,
    *,
    status: str = "started",
    max_total_rounds: int | None = None,
    cfg: HocaConfig | None = None,
    **fields: Any,
) -> Path:
    """Create ``status.json`` with workflow metadata and run-start fields."""
    ensure_run_layout(run_dir)
    cfg = cfg or load_config()
    data = workflow_fields_from_config(cfg)
    if max_total_rounds is not None:
        data["max_total_rounds"] = max_total_rounds
    data.update(
        {
            "status": status,
            "current_round": 0,
            "final_state": None,
            "pr_url": None,
            **fields,
        }
    )
    data.update(derived_status_fields(run_dir))
    path = status_path(run_dir)
    write_json_atomic(path, data)
    return path


def sync_status_fields(run_dir: Path) -> Path | None:
    """Refresh artifact-backed fields on an existing ``status.json``."""
    path = status_path(run_dir)
    if not path.is_file():
        return None
    data = merge_status_snapshot(run_dir, {})
    write_json_atomic(path, data)
    return path


def write_status(run_dir: Path, status: str, **fields: Any) -> Path:
    data = merge_status_snapshot(run_dir, {"status": status, **fields})
    path = status_path(run_dir)
    write_json_atomic(path, data)
    return path


def mark_failed(run_dir: Path, reason: str) -> Path:
    return write_status(run_dir, "failed", reason=reason, failed_at=now_iso())


def mark_blocked(run_dir: Path, reason: str) -> Path:
    return write_status(run_dir, "blocked", reason=reason, blocked_at=now_iso())


def is_duplicate_issue_run(project_path: Path, issue_id: str) -> bool:
    lock_path = project_path / RUN_STATE_DIRNAME / "runs" / f"issue-{issue_id}.lock"
    return lock_path.exists()


def acquire_lock(lock_path: Path, metadata: dict[str, Any]) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    stat_result = os.fstat(fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, sort_keys=True)
            f.write("\n")
    except BaseException:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _held_locks.append(lock_path)
    _held_lock_ids[lock_path] = (stat_result.st_dev, stat_result.st_ino)
    return True


def release_lock(lock_path: Path) -> None:
    if lock_path in _held_locks:
        expected_id = _held_lock_ids.get(lock_path)
        try:
            current = lock_path.stat()
        except FileNotFoundError:
            pass
        else:
            current_id = (current.st_dev, current.st_ino)
            if expected_id is None or current_id == expected_id:
                lock_path.unlink()
        _held_locks.remove(lock_path)
        _held_lock_ids.pop(lock_path, None)


def _cleanup_locks() -> None:
    for lp in list(_held_locks):
        release_lock(lp)
    _held_locks.clear()
    _held_lock_ids.clear()


def _signal_handler(signum: int, _frame: Any) -> None:
    _cleanup_locks()
    raise SystemExit(128 + signum)


atexit.register(_cleanup_locks)
for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, _signal_handler)
