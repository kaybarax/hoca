"""Disposable Git worktree management for HOCA sandbox runs.

When enabled, HOCA creates a temporary worktree under
``.hoca-runtime/worktrees/<run_id>/`` so the worker/reviewer operate on a
disposable copy rather than the user's active checkout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

WORKTREES_SUBDIR = "worktrees"


def worktree_base(project_path: Path) -> Path:
    return project_path / ".hoca-runtime" / WORKTREES_SUBDIR


def worktree_path(project_path: Path, run_id: str) -> Path:
    return worktree_base(project_path) / run_id


def validate_worktree_path(project_path: Path, candidate: Path) -> bool:
    """Return True only if *candidate* is safely inside the runtime worktree dir."""
    try:
        resolved_base = worktree_base(project_path).resolve()
        resolved_candidate = candidate.resolve()
        return resolved_candidate == resolved_base or str(resolved_candidate).startswith(
            str(resolved_base) + "/"
        )
    except (OSError, ValueError):
        return False


def create_worktree(
    project_path: Path,
    run_id: str,
    branch: str,
) -> Path:
    """Create a disposable worktree for *branch* and return its path."""
    wt = worktree_path(project_path, run_id)
    if wt.exists():
        raise FileExistsError(f"Worktree already exists: {wt}")
    wt.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=str(project_path),
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    command = ["git", "worktree", "add"]
    if branch_exists:
        command.extend([str(wt), branch])
    else:
        command.extend(["-b", branch, str(wt)])
    subprocess.run(
        command,
        cwd=str(project_path),
        check=True,
        capture_output=True,
        text=True,
    )
    return wt


def remove_worktree(project_path: Path, run_id: str) -> bool:
    """Remove a disposable worktree. Returns True if cleaned up, False if not found."""
    wt = worktree_path(project_path, run_id)
    if not validate_worktree_path(project_path, wt):
        raise ValueError(f"Worktree path escapes runtime directory: {wt}")
    if not wt.exists():
        _prune_worktrees(project_path)
        return False
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt)],
        cwd=str(project_path),
        check=False,
        capture_output=True,
        text=True,
    )
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    _prune_worktrees(project_path)
    return True


def _prune_worktrees(project_path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(project_path),
        check=False,
        capture_output=True,
        text=True,
    )


def worktree_diff(project_path: Path, run_id: str) -> str:
    """Return the diff of uncommitted changes inside the worktree."""
    wt = worktree_path(project_path, run_id)
    if not wt.exists():
        return ""
    result = subprocess.run(
        ["git", "diff"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def worktree_changed_files(project_path: Path, run_id: str) -> list[str]:
    """Return list of changed file paths in the worktree."""
    wt = worktree_path(project_path, run_id)
    if not wt.exists():
        return []
    result = subprocess.run(
        ["git", "status", "--short", "--porcelain"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
    )
    files = []
    for line in result.stdout.splitlines():
        if line.strip():
            path = line[3:].strip()
            if path and not path.startswith(".hoca-runtime"):
                files.append(path)
    return files
