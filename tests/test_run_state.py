from __future__ import annotations

import json
import os
from pathlib import Path


from hoca.run_state import (
    RUN_STATE_DIRNAME,
    _held_locks,
    acquire_lock,
    create_run_id,
    current_round,
    ensure_gitignore,
    ensure_run_dir,
    ensure_runtime_dirs,
    is_duplicate_issue_run,
    mark_blocked,
    mark_failed,
    now_epoch,
    now_iso,
    read_json,
    read_optional_json,
    release_lock,
    write_json,
    write_json_atomic,
    write_status,
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
    for subdir in ("attempts", "reviews", "decisions", "validation", "logs"):
        assert (run_dir / subdir).is_dir()


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


def test_write_json_atomic_writes_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "atomic.json"
    write_json_atomic(path, {"status": "running"})
    assert path.is_file()
    assert not (tmp_path / "atomic.json.tmp").exists()
    assert read_json(path)["status"] == "running"


def test_read_optional_json_missing_file(tmp_path: Path) -> None:
    assert read_optional_json(tmp_path / "missing.json") is None


def test_current_round_counts_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-rounds"
    run_dir.mkdir()
    attempts = run_dir / "attempts"
    attempts.mkdir()
    (attempts / "worker-attempt-1.json").write_text("{}", encoding="utf-8")
    (attempts / "worker-attempt-2.json").write_text("{}", encoding="utf-8")
    assert current_round(run_dir, prefix="worker-attempt-", subdir="attempts") == 2


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


def test_release_lock_does_not_remove_unheld_lock(tmp_path: Path) -> None:
    lock = tmp_path / "foreign.lock"
    lock.write_text(json.dumps({"pid": 2}), encoding="utf-8")

    release_lock(lock)

    assert lock.exists()
    assert json.loads(lock.read_text(encoding="utf-8")) == {"pid": 2}


def test_release_lock_does_not_remove_replaced_lock(tmp_path: Path) -> None:
    lock = tmp_path / "replaced.lock"
    acquire_lock(lock, {"pid": 1})
    lock.unlink()
    lock.write_text(json.dumps({"pid": 2}), encoding="utf-8")

    release_lock(lock)

    assert lock.exists()
    assert json.loads(lock.read_text(encoding="utf-8")) == {"pid": 2}


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


# --- ensure_runtime_dirs ---


def test_ensure_runtime_dirs_creates_structure(tmp_path: Path) -> None:
    runtime = ensure_runtime_dirs(tmp_path)
    assert runtime == tmp_path / RUN_STATE_DIRNAME
    assert (runtime / "runs").is_dir()
    assert (runtime / "logs").is_dir()


def test_ensure_runtime_dirs_idempotent(tmp_path: Path) -> None:
    r1 = ensure_runtime_dirs(tmp_path)
    r2 = ensure_runtime_dirs(tmp_path)
    assert r1 == r2
    assert (r1 / "runs").is_dir()
    assert (r1 / "logs").is_dir()


# --- ensure_gitignore ---


def test_ensure_gitignore_creates_rule(tmp_path: Path) -> None:
    added = ensure_gitignore(tmp_path)
    assert added is True
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".hoca-runtime/" in content


def test_ensure_gitignore_appends_to_existing(tmp_path: Path) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text("node_modules/\n", encoding="utf-8")
    ensure_gitignore(tmp_path)
    content = gi.read_text(encoding="utf-8")
    assert "node_modules/" in content
    assert ".hoca-runtime/" in content


def test_ensure_gitignore_no_duplicate(tmp_path: Path) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text(".hoca-runtime/\n", encoding="utf-8")
    added = ensure_gitignore(tmp_path)
    assert added is False
    lines = gi.read_text(encoding="utf-8").splitlines()
    assert lines.count(".hoca-runtime/") == 1


# --- now_iso ---


def test_now_iso_format() -> None:
    ts = now_iso()
    assert ts.endswith("Z")
    assert "T" in ts
    assert len(ts) == 20


# --- write_status ---


def test_write_status_creates_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    path = write_status(run_dir, "started", run_id="run-1", task="fix bug")
    assert path == run_dir / "status.json"
    data = read_json(path)
    assert data["status"] == "started"
    assert data["run_id"] == "run-1"
    assert data["task"] == "fix bug"


def test_write_status_updates_existing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-2"
    run_dir.mkdir()
    write_status(run_dir, "started", run_id="run-2")
    write_status(run_dir, "running")
    data = read_json(run_dir / "status.json")
    assert data["status"] == "running"
    assert data["run_id"] == "run-2"


# --- mark_failed ---


def test_mark_failed_writes_reason(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-fail"
    run_dir.mkdir()
    write_status(run_dir, "started", run_id="run-fail")
    mark_failed(run_dir, "tests_failed")
    data = read_json(run_dir / "status.json")
    assert data["status"] == "failed"
    assert data["reason"] == "tests_failed"
    assert "failed_at" in data


# --- mark_blocked ---


def test_mark_blocked_writes_reason(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-block"
    run_dir.mkdir()
    write_status(run_dir, "started", run_id="run-block")
    mark_blocked(run_dir, "dirty_working_tree")
    data = read_json(run_dir / "status.json")
    assert data["status"] == "blocked"
    assert data["reason"] == "dirty_working_tree"
    assert "blocked_at" in data


# --- is_duplicate_issue_run ---


def test_is_duplicate_issue_run_false(tmp_path: Path) -> None:
    ensure_runtime_dirs(tmp_path)
    assert is_duplicate_issue_run(tmp_path, "42") is False


def test_is_duplicate_issue_run_true(tmp_path: Path) -> None:
    ensure_runtime_dirs(tmp_path)
    lock = tmp_path / RUN_STATE_DIRNAME / "runs" / "issue-42.lock"
    lock.write_text(json.dumps({"run_id": "issue-42"}))
    assert is_duplicate_issue_run(tmp_path, "42") is True
