from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hoca.config import DEFAULT_POLICY, PolicyError, SafetyPolicy
from hoca.security import is_secret_like_path
from hoca.subprocess_utils import run_command


MAIN_BRANCHES = {"main", "master"}


@dataclass(frozen=True)
class GitChange:
    status: str
    path: str


def parse_status_porcelain(output: str) -> list[GitChange]:
    changes: list[GitChange] = []
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changes.append(GitChange(status=status, path=path))
    return changes


def is_git_repo(path: Path) -> bool:
    result = run_command(["git", "rev-parse", "--git-dir"], cwd=path)
    return result.ok


def repo_root(path: Path) -> Path:
    result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if not result.ok:
        raise PolicyError("Not inside a Git repository.")
    return Path(result.stdout.strip())


def current_branch(path: Path) -> str:
    result = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    if not result.ok:
        raise PolicyError("Unable to determine current branch.")
    return result.stdout.strip()


def working_tree_status(path: Path) -> list[GitChange]:
    result = run_command(["git", "status", "--short"], cwd=path)
    if not result.ok:
        raise PolicyError(result.stderr.strip() or "Unable to read git status.")
    return parse_status_porcelain(result.stdout)


def is_working_tree_clean(path: Path) -> bool:
    return len(working_tree_status(path)) == 0


def changed_files(path: Path) -> list[str]:
    result = run_command(["git", "diff", "--name-only"], cwd=path)
    if not result.ok:
        raise PolicyError("Unable to list changed files.")
    return [line for line in result.stdout.splitlines() if line]


def staged_files(path: Path) -> list[str]:
    result = run_command(["git", "diff", "--cached", "--name-only"], cwd=path)
    if not result.ok:
        raise PolicyError("Unable to list staged files.")
    return [line for line in result.stdout.splitlines() if line]


def has_merge_conflicts(path: Path) -> bool:
    result = run_command(["git", "diff", "--name-only", "--diff-filter=U"], cwd=path)
    if not result.ok:
        return False
    return bool(result.stdout.strip())


def create_branch(path: Path, branch_name: str) -> None:
    if not branch_name or not branch_name.strip():
        raise PolicyError("Branch name must not be empty.")
    result = run_command(["git", "checkout", "-b", branch_name], cwd=path)
    if not result.ok:
        raise PolicyError(result.stderr.strip() or f"Unable to create branch: {branch_name}")


def read_git_changes(repo_path: Path) -> list[GitChange]:
    return working_tree_status(repo_path)


def assert_clean_working_tree(
    repo_path: Path,
    *,
    policy: SafetyPolicy = DEFAULT_POLICY,
    allowed_changes: set[str] | None = None,
) -> None:
    changes = read_git_changes(repo_path)
    if not changes:
        return

    allowed = allowed_changes or set()
    secret_changes = [change.path for change in changes if is_secret_like_path(change.path)]
    if secret_changes and policy.stop_on_secret_changes:
        raise PolicyError(f"Secret-like files are modified or created: {', '.join(secret_changes)}")

    unrelated = [change.path for change in changes if change.path not in allowed]
    if unrelated and policy.stop_on_unrelated_changes:
        raise PolicyError(f"Working tree contains unrelated changes: {', '.join(unrelated)}")

    if policy.require_clean_working_tree:
        raise PolicyError("Working tree must be clean before each run.")


def assert_branch_allows_push(branch: str, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
    if branch in MAIN_BRANCHES and policy.forbid_direct_push_to_main:
        raise PolicyError(f"Direct pushes to {branch} are forbidden by default.")


def build_stage_command(paths: list[str]) -> list[str]:
    if not paths:
        raise PolicyError("Refusing to stage without explicit paths.")
    forbidden = {".", "-A", "--all", ":/"}
    if any(path in forbidden for path in paths):
        raise PolicyError("Blind staging is forbidden; pass explicit relevant paths.")
    secret_paths = [path for path in paths if is_secret_like_path(path)]
    if secret_paths:
        raise PolicyError(f"Refusing to stage secret-like files: {', '.join(secret_paths)}")
    return ["git", "add", "--", *paths]


def build_commit_command(message: str) -> list[str]:
    if not message.strip():
        raise PolicyError("Commit message is required.")
    return ["git", "commit", "-m", message]


def reject_unsafe_git_command(command: list[str]) -> None:
    if command[:2] == ["git", "add"] and any(
        arg in {".", "-A", "--all", ":/"} for arg in command[2:]
    ):
        raise PolicyError("Never use git add . or equivalent blind staging.")
    if command[:2] == ["git", "add"] and any(is_secret_like_path(arg) for arg in command[2:]):
        raise PolicyError("Never stage secret-like files.")
    if command[:2] == ["git", "commit"] and any(
        arg == "-am" or arg.startswith("-am") for arg in command[2:]
    ):
        raise PolicyError("Never use git commit -am.")
