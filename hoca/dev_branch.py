from __future__ import annotations

import argparse
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


PROJECT_CONFIG_CANDIDATES = (
    ".hoca/config.toml",
    ".hoca.toml",
)


@dataclass(frozen=True)
class DevBranchResolution:
    branch: str
    source: str


def _run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _config_value(data: dict[str, object]) -> str:
    raw = data.get("dev_branch")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    hoca = data.get("hoca")
    if isinstance(hoca, dict):
        raw = hoca.get("dev_branch")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    git = data.get("git")
    if isinstance(git, dict):
        raw = git.get("dev_branch")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    return ""


def dev_branch_from_project_config(repo_root: Path) -> DevBranchResolution | None:
    for relative in PROJECT_CONFIG_CANDIDATES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        branch = _config_value(data)
        if branch:
            return DevBranchResolution(branch=branch, source=relative)
    return None


def dev_branch_from_origin_head(repo_root: Path) -> DevBranchResolution | None:
    ref = _run_git(repo_root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if ref.startswith("origin/"):
        return DevBranchResolution(branch=ref.removeprefix("origin/"), source="origin/HEAD")
    return None


def current_branch_fallback(repo_root: Path) -> DevBranchResolution | None:
    branch = _run_git(repo_root, "branch", "--show-current")
    if branch:
        return DevBranchResolution(branch=branch, source="current branch")
    return None


def resolve_dev_branch(
    repo_root: Path,
    *,
    explicit: str = "",
) -> DevBranchResolution | None:
    if explicit.strip():
        return DevBranchResolution(branch=explicit.strip(), source="CLI override")

    repo_root = repo_root.resolve()
    return (
        dev_branch_from_project_config(repo_root)
        or dev_branch_from_origin_head(repo_root)
        or current_branch_fallback(repo_root)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a target repository development branch.")
    parser.add_argument("project_path")
    parser.add_argument("--dev-branch", default="")
    parser.add_argument("--show-source", action="store_true")
    args = parser.parse_args(argv)

    resolution = resolve_dev_branch(Path(args.project_path), explicit=args.dev_branch)
    if resolution is None:
        return 1
    if args.show_source:
        print(f"{resolution.branch}\t{resolution.source}")
    else:
        print(resolution.branch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
