from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoca.worktree_pool import (
    WorktreeLeasePool,
    generate_lane_branch,
    prune_orphaned_worktrees,
    slugify,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HOCA Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_worktree_lease_roundtrip_and_stale_cleanup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    branch = "feat/task-a"
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    pool = WorktreeLeasePool(control_root=tmp_path / "control")
    lease = pool.create_lease(
        lane_id="lane-task-a-01",
        project_id="proj-a",
        task_id="task-a",
        branch=branch,
        base_ref="main",
        project_path=repo,
        lease_id="lane-task-a-01",
    )
    assert lease.lease_id == "lane-task-a-01"
    assert lease.worktree_path == str(
        (repo / ".hoca-runtime" / "worktrees" / "lane-task-a-01").resolve()
    )
    assert pool.get_lease("lane-task-a-01") is not None
    assert len(pool.stale_leases()) == 0

    renewed = pool.renew_lease("lane-task-a-01")
    assert renewed.heartbeat_at is not None

    # release refuses to remove dirty worktree unless forced.
    wt = Path(lease.worktree_path)
    (wt / "file.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to remove unclean worktree"):
        pool.release_lease("lane-task-a-01", project_path=repo)

    assert pool.release_lease("lane-task-a-01", project_path=repo, force=True)
    assert pool.get_lease("lane-task-a-01") is None


def test_create_lease_does_not_touch_active_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    branch = "feat/task-b"
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    before_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    before_diff = subprocess.run(
        ["git", "diff", "--name-only"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout

    pool = WorktreeLeasePool(control_root=tmp_path / "control")
    pool.create_lease(
        lane_id="lane-task-b-01",
        project_id="proj-b",
        task_id="task-b",
        branch=branch,
        base_ref="main",
        project_path=repo,
        lease_id="lane-task-b-01",
    )

    after_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    after_diff = subprocess.run(
        ["git", "diff", "--name-only"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout

    assert after_branch == before_branch == "main"
    assert after_diff == before_diff == ""


def test_branch_generation_avoids_collisions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    # create branch that may collide with the first generated candidate.
    subprocess.run(
        ["git", "branch", "hoca/fix-login-abc123"], cwd=repo, check=True, capture_output=True
    )

    first = generate_lane_branch(repo, "fix login", "abc123")
    assert first.startswith("hoca/fix-login-abc123")
    assert first != "hoca/fix-login-abc123"


def test_generate_lane_branch_produces_git_ref_safe_name(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    candidate = generate_lane_branch(repo, "Fix login flow! (critical)", "xyz")
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", candidate], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0


def test_cleanup_stale_metadata_is_removed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    branch = "feat/task-c"
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    pool = WorktreeLeasePool(control_root=tmp_path / "control", ttl_seconds=3600)
    pool.create_lease(
        lane_id="lane-task-c-01",
        project_id="proj-c",
        task_id="task-c",
        branch=branch,
        base_ref="main",
        project_path=repo,
        lease_id="lane-task-c-01",
    )

    assert pool.get_lease("lane-task-c-01") is not None
    removed = pool.cleanup_stale_worktrees(
        project_path=repo,
        reference="2100-01-01T00:00:00Z",
        remove_completed=False,
        remove_abandoned=False,
    )
    assert removed == ["lane-task-c-01"]
    assert pool.get_lease("lane-task-c-01") is None


def test_cleanup_stale_worktrees_ignores_unmanaged_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    branch = "feat/task-d"
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    pool = WorktreeLeasePool(control_root=tmp_path / "control", ttl_seconds=3600)
    pool.create_lease(
        lane_id="lane-task-d-01",
        project_id="proj-d",
        task_id="task-d",
        branch=branch,
        base_ref="main",
        project_path=repo,
        lease_id="lane-task-d-01",
    )

    unmanaged = repo / ".hoca-runtime" / "worktrees" / "manual-worktree"
    unmanaged.mkdir(parents=True, exist_ok=True)

    removed = pool.cleanup_stale_worktrees(
        project_path=repo,
        reference="2100-01-01T00:00:00Z",
        remove_completed=False,
        remove_abandoned=False,
    )

    assert removed == ["lane-task-d-01"]
    assert unmanaged.exists()
    assert pool.get_lease("lane-task-d-01") is None


def test_prune_orphaned_worktrees(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    base = repo / ".hoca-runtime" / "worktrees"
    base.mkdir(parents=True)
    managed = base / "managed"
    orphan = base / "orphan"
    managed.mkdir()
    orphan.mkdir()

    result = prune_orphaned_worktrees(repo, managed_roots=[str(managed)], dry_run=False)
    assert str(orphan) in result
    assert not orphan.exists()
    assert managed.exists()


def test_worktree_slug_and_helpers() -> None:
    assert slugify("Fix login flow!") == "fix-login-flow"
    assert "task" == slugify("")
