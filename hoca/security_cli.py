"""CLI entry points so shell scripts reuse hoca.security checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hoca.security import is_secret_like_path, validate_staging_file_list


def _read_path_list(file_list: Path) -> list[str]:
    paths: list[str] = []
    for line in file_list.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            paths.append(stripped)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reuse hoca.security checks from shell scripts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    secret_parser = subparsers.add_parser(
        "is-secret-like",
        help="Exit 0 when the path looks secret-like, 1 otherwise.",
    )
    secret_parser.add_argument("path")

    validate_path_parser = subparsers.add_parser(
        "validate-path",
        help="Validate one repo-relative path for safe staging.",
    )
    validate_path_parser.add_argument("repo_root")
    validate_path_parser.add_argument("path")

    validate_list_parser = subparsers.add_parser(
        "validate-staging",
        help="Validate every path listed in a newline-delimited file.",
    )
    validate_list_parser.add_argument("repo_root")
    validate_list_parser.add_argument("file_list")

    args = parser.parse_args(argv)

    if args.command == "is-secret-like":
        return 0 if is_secret_like_path(args.path) else 1

    if args.command == "validate-path":
        errors = validate_staging_file_list(args.repo_root, [args.path])
    else:
        file_list = Path(args.file_list)
        if not file_list.is_file():
            print(f"File list not found: {file_list}", file=sys.stderr)
            return 1
        errors = validate_staging_file_list(args.repo_root, _read_path_list(file_list))

    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
