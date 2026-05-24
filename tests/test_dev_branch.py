from __future__ import annotations

import subprocess
from pathlib import Path

from hoca.dev_branch import resolve_dev_branch


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def test_explicit_dev_branch_wins(tmp_path: Path) -> None:
    init_repo(tmp_path)

    resolution = resolve_dev_branch(tmp_path, explicit="release")

    assert resolution is not None
    assert resolution.branch == "release"
    assert resolution.source == "CLI override"


def test_project_config_defines_dev_branch(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".hoca").mkdir()
    (tmp_path / ".hoca" / "config.toml").write_text('dev_branch = "develop"\n', encoding="utf-8")

    resolution = resolve_dev_branch(tmp_path)

    assert resolution is not None
    assert resolution.branch == "develop"
    assert resolution.source == ".hoca/config.toml"


def test_current_branch_is_fallback(tmp_path: Path) -> None:
    init_repo(tmp_path)
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    resolution = resolve_dev_branch(tmp_path)

    assert resolution is not None
    assert resolution.branch == current
    assert resolution.source == "current branch"
