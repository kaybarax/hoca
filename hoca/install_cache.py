from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


LOCKFILE_CANDIDATES = (
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
)


def marker_path(project_path: Path, manager: str) -> Path:
    return project_path / ".hoca-runtime" / "install-cache" / f"{manager}.sha256"


def _command_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return f"{command}:missing"
    return f"{command}:{result.stdout.strip() or result.stderr.strip() or result.returncode}"


def install_fingerprint(project_path: Path, manager: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"manager:{manager}\0".encode())
    for name in LOCKFILE_CANDIDATES:
        candidate = project_path / name
        if not candidate.is_file():
            continue
        digest.update(f"file:{name}\0".encode())
        digest.update(candidate.read_bytes())
        digest.update(b"\0")
    for command in ("node", manager, "python3"):
        digest.update(_command_version(command).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def install_current(project_path: Path, manager: str) -> bool:
    if not (project_path / "node_modules").is_dir():
        return False
    marker = marker_path(project_path, manager)
    if not marker.is_file():
        return False
    return marker.read_text(encoding="utf-8").strip() == install_fingerprint(
        project_path, manager
    )


def write_marker(project_path: Path, manager: str) -> Path:
    marker = marker_path(project_path, manager)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(install_fingerprint(project_path, manager) + "\n", encoding="utf-8")
    return marker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HOCA dependency install cache helper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("current", "mark"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("project_path", type=Path)
        subparser.add_argument("manager")
    args = parser.parse_args(argv)

    project_path = args.project_path.resolve()
    if args.command == "current":
        return 0 if install_current(project_path, args.manager) else 1
    if args.command == "mark":
        write_marker(project_path, args.manager)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
