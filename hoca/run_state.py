from __future__ import annotations

import atexit
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

RUN_STATE_DIRNAME = ".hoca-runtime"

_held_locks: list[Path] = []


def now_epoch() -> int:
    return int(time.time())


def create_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{now_epoch()}"


def ensure_run_dir(project_path: Path, run_id: str) -> Path:
    run_dir = project_path / RUN_STATE_DIRNAME / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
