from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject
from hoca.fleet_registry import FleetRegistry


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, stdout=subprocess.PIPE)


def test_create_project_enforces_git_and_uniqueness(tmp_path: Path) -> None:
    repo = tmp_path / "repo-one"
    repo.mkdir()
    _init_git_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")

    project = HocaProject(
        project_id="p1",
        repo_path=str(repo),
        default_branch="main",
        max_parallel_tasks=2,
    )
    registry.create_project(project)
    assert registry.get_project("p1") == project

    with pytest.raises(ValueError, match="already exists"):
        registry.create_project(project)

    # duplicate repo path
    clone = tmp_path / "repo-two"
    clone.mkdir()
    _init_git_repo(clone)
    duplicate_repo = HocaProject(project_id="p2", repo_path=str(repo), default_branch="main")
    with pytest.raises(ValueError, match="already registered"):
        registry.create_project(duplicate_repo)

    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    non_git = HocaProject(project_id="p3", repo_path=str(not_a_repo), default_branch="main")
    with pytest.raises(ValueError, match="must exist"):
        registry.create_project(non_git)


def test_delete_project_cleans_project_tasks_and_lanes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(HocaProject(project_id="p1", repo_path=str(repo), default_branch="main"))

    task = HocaFleetTask(
        task_id="t1",
        project_id="p1",
        status="queued",
        readiness="not_ready",
    )
    registry.create_task(task)
    assert registry.get_task("t1") is not None

    lane = HocaLane(
        lane_id="l1",
        task_id="t1",
        project_id="p1",
        status="allocated",
        branch="hoca/p1-t1",
        attempt_number=0,
    )
    registry.create_lane(lane)
    assert registry.get_lane("l1") is not None

    registry.delete_project("p1")
    assert registry.get_project("p1") is None
    assert registry.get_task("t1") is None
    assert registry.get_lane("l1") is None


def test_task_dependency_and_project_reference_validation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(HocaProject(project_id="p1", repo_path=str(repo), default_branch="main"))

    parent = HocaFleetTask(task_id="parent", project_id="p1", status="queued", readiness="ready")
    registry.create_task(parent)

    child = HocaFleetTask(
        task_id="child",
        project_id="p1",
        status="queued",
        readiness="not_ready",
        dependencies=["parent"],
    )
    registry.create_task(child)

    bad = HocaFleetTask(
        task_id="bad",
        project_id="p1",
        status="queued",
        readiness="not_ready",
        dependencies=["missing"],
    )
    with pytest.raises(ValueError, match="Unknown dependency"):
        registry.create_task(bad)

    missing = HocaFleetTask(task_id="unknown", project_id="not-a-project", status="queued", readiness="ready")
    with pytest.raises(ValueError, match="unknown project"):
        registry.create_task(missing)


def test_lane_requires_existing_task_and_project(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(HocaProject(project_id="p1", repo_path=str(repo), default_branch="main"))

    task = HocaFleetTask(task_id="t1", project_id="p1", status="queued", readiness="ready")
    registry.create_task(task)

    bad_lane = HocaLane(
        lane_id="missing-task",
        task_id="bad",
        project_id="p1",
        status="allocated",
        branch="hoca/fail",
        attempt_number=0,
    )
    with pytest.raises(ValueError, match="unknown project or task"):
        registry.create_lane(bad_lane)

    lane = HocaLane(
        lane_id="good-lane",
        task_id="t1",
        project_id="p1",
        status="allocated",
        branch="hoca/good",
        attempt_number=0,
    )
    registry.create_lane(lane)

    lanes = registry.list_lanes(project_id="p1")
    assert [item.lane_id for item in lanes] == ["good-lane"]
