from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def runtime_dir(project_path: Path) -> Path:
    return project_path / ".hoca-runtime"


def runs_dir(project_path: Path) -> Path:
    return runtime_dir(project_path) / "runs"
