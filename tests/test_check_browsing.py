from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hoca.paths import repo_root


SCRIPT = str(repo_root() / "scripts" / "check-browsing.sh")


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


def run_check(
    run_dir: Path, *extra_args: str, env_override: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [SCRIPT, str(run_dir), *extra_args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_browsing_available_when_caps_file_has_enable_browsing(run_dir: Path) -> None:
    (run_dir / "openhands-capabilities.txt").write_text("headless,task,enable-browsing\n")

    result = run_check(run_dir)

    assert result.returncode == 0
    assert "available" in result.stdout.lower()
    assert (run_dir / "browsing-available.txt").read_text().strip() == "true"


def test_browsing_unavailable_when_caps_file_missing_enable_browsing(run_dir: Path) -> None:
    (run_dir / "openhands-capabilities.txt").write_text("headless,task,json\n")

    result = run_check(run_dir)

    assert result.returncode == 0
    assert "not available" in result.stdout.lower()
    assert (run_dir / "browsing-available.txt").read_text().strip() == "false"


def test_require_flag_exits_nonzero_when_browsing_unavailable(run_dir: Path) -> None:
    (run_dir / "openhands-capabilities.txt").write_text("headless,task\n")

    result = run_check(run_dir, "--require")

    assert result.returncode != 0
    assert "engineer should provide" in result.stdout.lower()


def test_require_flag_exits_zero_when_browsing_available(run_dir: Path) -> None:
    (run_dir / "openhands-capabilities.txt").write_text("headless,task,enable-browsing\n")

    result = run_check(run_dir, "--require")

    assert result.returncode == 0


def test_browsing_available_file_is_written(run_dir: Path) -> None:
    (run_dir / "openhands-capabilities.txt").write_text("headless,task\n")

    run_check(run_dir)

    assert (run_dir / "browsing-available.txt").exists()
    assert (run_dir / "browsing-available.txt").read_text().strip() in ("true", "false")
