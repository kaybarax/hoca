from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoca.worktree import (
    create_worktree,
    remove_worktree,
    validate_worktree_path,
    worktree_base,
    worktree_changed_files,
    worktree_diff,
    worktree_path,
)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feat/test-task"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


class TestWorktreePath:
    def test_path_under_runtime(self, tmp_path: Path) -> None:
        result = worktree_path(tmp_path, "run-123")
        assert str(result).endswith(".hoca-runtime/worktrees/run-123")

    def test_base_under_runtime(self, tmp_path: Path) -> None:
        result = worktree_base(tmp_path)
        assert result == tmp_path / ".hoca-runtime" / "worktrees"


class TestValidateWorktreePath:
    def test_valid_path(self, tmp_path: Path) -> None:
        base = worktree_base(tmp_path)
        base.mkdir(parents=True)
        candidate = base / "run-123"
        assert validate_worktree_path(tmp_path, candidate) is True

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        candidate = tmp_path / ".." / "escape"
        assert validate_worktree_path(tmp_path, candidate) is False

    def test_unrelated_path_rejected(self, tmp_path: Path) -> None:
        assert validate_worktree_path(tmp_path, Path("/tmp/evil")) is False

    def test_base_itself_is_valid(self, tmp_path: Path) -> None:
        base = worktree_base(tmp_path)
        base.mkdir(parents=True)
        assert validate_worktree_path(tmp_path, base) is True


class TestCreateWorktree:
    def test_creates_worktree(self, git_repo: Path) -> None:
        wt = create_worktree(git_repo, "run-001", "feat/test-task")
        assert wt.exists()
        assert (wt / "README.md").exists()
        assert wt == worktree_path(git_repo, "run-001")

    def test_duplicate_raises(self, git_repo: Path) -> None:
        create_worktree(git_repo, "run-dup", "feat/test-task")
        with pytest.raises(FileExistsError):
            create_worktree(git_repo, "run-dup", "feat/test-task")

    def test_worktree_is_on_correct_branch(self, git_repo: Path) -> None:
        wt = create_worktree(git_repo, "run-branch", "feat/test-task")
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "feat/test-task"


class TestRemoveWorktree:
    def test_removes_existing(self, git_repo: Path) -> None:
        wt = create_worktree(git_repo, "run-rm", "feat/test-task")
        assert wt.exists()
        removed = remove_worktree(git_repo, "run-rm")
        assert removed is True
        assert not wt.exists()

    def test_nonexistent_returns_false(self, git_repo: Path) -> None:
        removed = remove_worktree(git_repo, "run-nonexistent")
        assert removed is False

    def test_escape_path_raises(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import hoca.worktree as wt_mod

        monkeypatch.setattr(
            wt_mod, "worktree_path", lambda pp, rid: pp / ".." / "escape"
        )
        with pytest.raises(ValueError, match="escapes runtime directory"):
            remove_worktree(git_repo, "evil")


class TestWorktreeDiff:
    def test_diff_shows_changes(self, git_repo: Path) -> None:
        wt = create_worktree(git_repo, "run-diff", "feat/test-task")
        (wt / "README.md").write_text("hello\nmodified\n")
        diff = worktree_diff(git_repo, "run-diff")
        assert "modified" in diff

    def test_no_worktree_returns_empty(self, git_repo: Path) -> None:
        assert worktree_diff(git_repo, "run-gone") == ""


class TestWorktreeChangedFiles:
    def test_lists_changed_files(self, git_repo: Path) -> None:
        wt = create_worktree(git_repo, "run-cf", "feat/test-task")
        (wt / "app.py").write_text("print('hello')\n")
        files = worktree_changed_files(git_repo, "run-cf")
        assert "app.py" in files

    def test_excludes_hoca_runtime(self, git_repo: Path) -> None:
        wt = create_worktree(git_repo, "run-ex", "feat/test-task")
        runtime = wt / ".hoca-runtime"
        runtime.mkdir()
        (runtime / "status.json").write_text("{}\n")
        files = worktree_changed_files(git_repo, "run-ex")
        assert all(not f.startswith(".hoca-runtime") for f in files)

    def test_no_worktree_returns_empty(self, git_repo: Path) -> None:
        assert worktree_changed_files(git_repo, "run-missing") == []
