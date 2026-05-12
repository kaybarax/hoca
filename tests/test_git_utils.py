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
    parse_status_porcelain,
    reject_unsafe_git_command,
)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)


def test_parse_status_handles_renames() -> None:
    changes = parse_status_porcelain(" M README.md\nR  old.txt -> new.txt\n?? .env\n")
    assert [change.path for change in changes] == ["README.md", "new.txt", ".env"]


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


def test_direct_push_to_main_is_forbidden() -> None:
    with pytest.raises(PolicyError, match="main"):
        assert_branch_allows_push("main")


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


def test_unsafe_git_commands_are_rejected() -> None:
    reject_unsafe_git_command(build_commit_command("safe message"))

    with pytest.raises(PolicyError, match="git add"):
        reject_unsafe_git_command(["git", "add", "."])

    with pytest.raises(PolicyError, match="git commit -am"):
        reject_unsafe_git_command(["git", "commit", "-am", "message"])

    with pytest.raises(PolicyError, match="secret-like"):
        reject_unsafe_git_command(["git", "add", "--", ".npmrc"])
