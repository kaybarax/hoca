from __future__ import annotations

import atexit
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUN_STATE_DIRNAME = ".hoca-runtime"

_held_locks: list[Path] = []


def now_epoch() -> int:
    return int(time.time())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{now_epoch()}"


def ensure_run_dir(project_path: Path, run_id: str) -> Path:
    run_dir = project_path / RUN_STATE_DIRNAME / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(run_dir: Path, status: str, **fields: Any) -> Path:
    data: dict[str, Any] = {"status": status}
    data.update(fields)
    status_path = run_dir / "status.json"
    if status_path.exists():
        existing = read_json(status_path)
        existing.update(data)
        data = existing
    write_json(status_path, data)
    return status_path


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
    return True


def release_lock(lock_path: Path) -> None:
    if lock_path.exists():
        lock_path.unlink()
    if lock_path in _held_locks:
        _held_locks.remove(lock_path)


def _cleanup_locks() -> None:
    for lp in list(_held_locks):
        try:
            lp.unlink(missing_ok=True)
        except OSError:
            pass
    _held_locks.clear()


def _signal_handler(signum: int, _frame: Any) -> None:
    _cleanup_locks()
    raise SystemExit(128 + signum)


atexit.register(_cleanup_locks)
for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, _signal_handler)
