from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from hoca.config import DEFAULT_POLICY, PolicyError, SafetyPolicy
from hoca.security import is_secret_like_path


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


def read_git_changes(repo_path: Path) -> list[GitChange]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_path,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise PolicyError(result.stderr.strip() or "Unable to read git status.")
    return parse_status_porcelain(result.stdout)


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
    if command[:2] == ["git", "commit"] and any(
        arg == "-am" or arg.startswith("-am") for arg in command[2:]
    ):
        raise PolicyError("Never use git commit -am.")
