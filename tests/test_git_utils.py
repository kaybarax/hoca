from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoca.config import PolicyError
from hoca.git_utils import (
    assert_branch_allows_push,
    assert_clean_working_tree,
    build_commit_command,
    build_stage_command,
    changed_files,
    create_branch,
    current_branch,
    has_merge_conflicts,
    is_git_repo,
    is_working_tree_clean,
    parse_status_porcelain,
    reject_unsafe_git_command,
    repo_root,
    staged_files,
    working_tree_status,
)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)


def init_repo_with_commit(path: Path) -> None:
    init_repo(path)
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


# --- parse_status_porcelain ---


def test_parse_status_handles_renames() -> None:
    changes = parse_status_porcelain(" M README.md\nR  old.txt -> new.txt\n?? .env\n")
    assert [change.path for change in changes] == ["README.md", "new.txt", ".env"]


# --- is_git_repo ---


def test_is_git_repo_true(tmp_path: Path) -> None:
    init_repo(tmp_path)
    assert is_git_repo(tmp_path) is True


def test_is_git_repo_false(tmp_path: Path) -> None:
    assert is_git_repo(tmp_path) is False


# --- repo_root ---


def test_repo_root_returns_toplevel(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subdir = tmp_path / "a" / "b"
    subdir.mkdir(parents=True)
    assert repo_root(subdir) == tmp_path


def test_repo_root_raises_outside_repo(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="Not inside a Git repository"):
        repo_root(tmp_path)


# --- current_branch ---


def test_current_branch(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    branch = current_branch(tmp_path)
    assert branch in {"main", "master"}


# --- working_tree_status / is_working_tree_clean ---


def test_working_tree_clean_after_commit(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    assert is_working_tree_clean(tmp_path) is True
    assert working_tree_status(tmp_path) == []


def test_working_tree_dirty(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    (tmp_path / "new.txt").write_text("hello\n", encoding="utf-8")
    assert is_working_tree_clean(tmp_path) is False
    status = working_tree_status(tmp_path)
    assert any(c.path == "new.txt" for c in status)


# --- changed_files / staged_files ---


def test_changed_files(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    assert "README.md" in changed_files(tmp_path)
    assert staged_files(tmp_path) == []


def test_staged_files(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    (tmp_path / "README.md").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    assert "README.md" in staged_files(tmp_path)
    assert changed_files(tmp_path) == []


# --- has_merge_conflicts ---


def test_no_merge_conflicts(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    assert has_merge_conflicts(tmp_path) is False


# --- create_branch ---


def test_create_branch(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    create_branch(tmp_path, "feature/test")
    assert current_branch(tmp_path) == "feature/test"


def test_create_branch_empty_name(tmp_path: Path) -> None:
    init_repo_with_commit(tmp_path)
    with pytest.raises(PolicyError, match="Branch name must not be empty"):
        create_branch(tmp_path, "")


# --- assert_clean_working_tree ---


def test_clean_working_tree_required(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="unrelated changes"):
        assert_clean_working_tree(tmp_path)


def test_secret_like_changes_stop_first(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".env").write_text("TOKEN=value\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="Secret-like"):
        assert_clean_working_tree(tmp_path)


# --- assert_branch_allows_push ---


def test_direct_push_to_main_is_forbidden() -> None:
    with pytest.raises(PolicyError, match="main"):
        assert_branch_allows_push("main")


# --- build_stage_command ---


def test_stage_command_requires_explicit_paths() -> None:
    assert build_stage_command(["README.md"]) == ["git", "add", "--", "README.md"]

    with pytest.raises(PolicyError, match="Blind staging"):
        build_stage_command(["."])

    with pytest.raises(PolicyError, match="explicit paths"):
        build_stage_command([])


def test_stage_command_rejects_secret_like_paths() -> None:
    with pytest.raises(PolicyError, match="secret-like"):
        build_stage_command(["README.md", ".env"])

    with pytest.raises(PolicyError, match="secret-like"):
        build_stage_command([".github/secrets/prod"])


# --- reject_unsafe_git_command ---


def test_unsafe_git_commands_are_rejected() -> None:
    reject_unsafe_git_command(build_commit_command("safe message"))

    with pytest.raises(PolicyError, match="git add"):
        reject_unsafe_git_command(["git", "add", "."])

    with pytest.raises(PolicyError, match="git commit -am"):
        reject_unsafe_git_command(["git", "commit", "-am", "message"])

    with pytest.raises(PolicyError, match="secret-like"):
        reject_unsafe_git_command(["git", "add", "--", ".npmrc"])
