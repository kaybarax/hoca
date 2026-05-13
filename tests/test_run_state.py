from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from hoca.run_state import (
    RUN_STATE_DIRNAME,
    _held_locks,
    acquire_lock,
    create_run_id,
    ensure_run_dir,
    now_epoch,
    read_json,
    release_lock,
    write_json,
)


def test_run_state_dirname() -> None:
    assert RUN_STATE_DIRNAME == ".hoca-runtime"


def test_now_epoch_returns_int() -> None:
    result = now_epoch()
    assert isinstance(result, int)
    assert result > 0


def test_create_run_id_default_prefix() -> None:
    rid = create_run_id()
    assert rid.startswith("run-")
    epoch_part = rid.removeprefix("run-")
    assert epoch_part.isdigit()


def test_create_run_id_custom_prefix() -> None:
    rid = create_run_id(prefix="issue-42")
    assert rid.startswith("issue-42-")


def test_ensure_run_dir(tmp_path: Path) -> None:
    run_dir = ensure_run_dir(tmp_path, "run-001")
    assert run_dir == tmp_path / RUN_STATE_DIRNAME / "runs" / "run-001"
    assert run_dir.is_dir()


def test_ensure_run_dir_idempotent(tmp_path: Path) -> None:
    d1 = ensure_run_dir(tmp_path, "run-002")
    d2 = ensure_run_dir(tmp_path, "run-002")
    assert d1 == d2
    assert d1.is_dir()


def test_write_and_read_json(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    payload = {"status": "running", "run_id": "run-123"}
    write_json(p, payload)
    assert p.exists()
    loaded = read_json(p)
    assert loaded == payload


def test_write_json_sorted_keys(tmp_path: Path) -> None:
    p = tmp_path / "sorted.json"
    write_json(p, {"z": 1, "a": 2})
    raw = p.read_text(encoding="utf-8")
    assert raw.index('"a"') < raw.index('"z"')


def test_write_json_trailing_newline(tmp_path: Path) -> None:
    p = tmp_path / "nl.json"
    write_json(p, {"key": "val"})
    assert p.read_text(encoding="utf-8").endswith("\n")


def test_acquire_lock_success(tmp_path: Path) -> None:
    lock = tmp_path / "test.lock"
    meta = {"run_id": "run-1", "pid": 99}
    try:
        ok = acquire_lock(lock, meta)
        assert ok is True
        assert lock.exists()
        stored = json.loads(lock.read_text(encoding="utf-8"))
        assert stored == meta
    finally:
        release_lock(lock)


def test_acquire_lock_creates_parent_dirs(tmp_path: Path) -> None:
    lock = tmp_path / "deep" / "nested" / "test.lock"
    try:
        ok = acquire_lock(lock, {"pid": 1})
        assert ok is True
        assert lock.exists()
    finally:
        release_lock(lock)


def test_acquire_lock_fails_when_held(tmp_path: Path) -> None:
    lock = tmp_path / "held.lock"
    try:
        assert acquire_lock(lock, {"pid": 1}) is True
        assert acquire_lock(lock, {"pid": 2}) is False
    finally:
        release_lock(lock)


def test_acquire_lock_is_atomic(tmp_path: Path) -> None:
    lock = tmp_path / "atomic.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("occupied")
    ok = acquire_lock(lock, {"pid": 1})
    assert ok is False
    if lock in _held_locks:
        _held_locks.remove(lock)


def test_release_lock(tmp_path: Path) -> None:
    lock = tmp_path / "rel.lock"
    acquire_lock(lock, {"pid": 1})
    assert lock.exists()
    release_lock(lock)
    assert not lock.exists()


def test_release_lock_removes_from_held(tmp_path: Path) -> None:
    lock = tmp_path / "tracked.lock"
    acquire_lock(lock, {"pid": 1})
    assert lock in _held_locks
    release_lock(lock)
    assert lock not in _held_locks


def test_release_lock_missing_file_is_safe(tmp_path: Path) -> None:
    lock = tmp_path / "ghost.lock"
    release_lock(lock)


def test_lock_metadata_is_json(tmp_path: Path) -> None:
    lock = tmp_path / "meta.lock"
    meta = {"run_id": "run-5", "started_at": 1700000000}
    try:
        acquire_lock(lock, meta)
        parsed = json.loads(lock.read_text(encoding="utf-8"))
        assert parsed == meta
    finally:
        release_lock(lock)


def test_stale_lock_not_blindly_removed(tmp_path: Path) -> None:
    lock = tmp_path / "stale.lock"
    lock.write_text(json.dumps({"pid": 99999, "run_id": "old-run"}))
    ok = acquire_lock(lock, {"pid": os.getpid()})
    assert ok is False
    content = json.loads(lock.read_text(encoding="utf-8"))
    assert content["run_id"] == "old-run"
