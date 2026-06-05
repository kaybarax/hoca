from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from hoca.cli import main
from hoca.fleet_registry import FleetRegistry


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_scheduler_tick_cli_smoke_launches_lane(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    add_project = CliRunner().invoke(
        main,
        ["project", "add", str(repo), "--project-id", "project-cli"],
        env=env,
    )
    assert add_project.exit_code == 0

    add_task = CliRunner().invoke(
        main,
        ["task", "create", "project-cli", "CLI smoke task", "--task-id", "task-cli"],
        env=env,
    )
    assert add_task.exit_code == 0

    tick_result = CliRunner().invoke(main, ["scheduler", "tick"], env=env)
    assert tick_result.exit_code == 0
    assert "launch" in tick_result.output
    assert "task-cli" in tick_result.output

    status_result = CliRunner().invoke(main, ["scheduler", "status"], env=env)
    assert status_result.exit_code == 0
    assert "Projects: 1" in status_result.output
    assert "Queued Tasks: 0" in status_result.output
    assert "Running Lanes: 1" in status_result.output

    registry = FleetRegistry(control_root=control_root)
    lane = registry.list_lanes(task_id="task-cli")[0]
    assert lane.project_id == "project-cli"
    assert lane.run_dir
