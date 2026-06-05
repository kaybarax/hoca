from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hoca.fleet_contracts import HocaFleetTask, HocaProject, HocaResourceBudget
from hoca.fleet_registry import FleetRegistry
from hoca.resource_governor import ResourceGovernor
from hoca.scheduler import FleetScheduler, run_scheduler_loop, _resolve_lock_path


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HOCA Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _registry_with_project_and_tasks(tmp_path: Path) -> FleetRegistry:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(
        HocaProject(
            project_id="project-a",
            repo_path=str(repo),
            default_branch="main",
            max_parallel_tasks=1,
        )
    )
    task_a = HocaFleetTask(
        task_id="task-a", project_id="project-a", status="queued", readiness="not_ready", priority=1
    )
    task_b = HocaFleetTask(
        task_id="task-b", project_id="project-a", status="queued", readiness="not_ready", priority=1
    )
    registry.create_task(task_a)
    registry.create_task(task_b)
    return registry


def test_scheduler_launches_only_capacity(tmp_path: Path) -> None:
    registry = _registry_with_project_and_tasks(tmp_path)
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=1,
        max_parallel_lanes=1,
        max_agents=4,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    scheduler = FleetScheduler(
        registry=registry, governor=governor, control_root=tmp_path / "control"
    )
    decisions = scheduler.tick()

    assert any(decision.decision_type == "launch" for decision in decisions)
    assert len(decisions) == 1

    tasks = {item.task_id: item for item in registry.list_tasks()}
    assert tasks["task-a"].status == "running"


def test_scheduler_no_work_is_noop(tmp_path: Path) -> None:
    registry = _registry_with_project_and_tasks(tmp_path)
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=1,
        max_parallel_lanes=1,
        max_agents=4,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    scheduler = FleetScheduler(
        registry=registry, governor=governor, control_root=tmp_path / "control"
    )

    for task_id in ("task-a", "task-b"):
        registry.update_task(
            task_id,
            HocaFleetTask(
                task_id=task_id,
                project_id="project-a",
                status="completed",
                readiness="not_ready",
                priority=1,
            ),
        )

    decisions = scheduler.tick()
    assert decisions == []
    assert "task-a" not in [item.task_id for item in registry.list_lanes()]


def test_scheduler_non_overlapping_tasks_can_run_together_when_capacity_allows(
    tmp_path: Path,
) -> None:
    registry = _registry_with_project_and_tasks(tmp_path)
    registry.update_project(
        "project-a",
        HocaProject(
            project_id="project-a",
            repo_path=str(tmp_path / "repo"),
            default_branch="main",
            max_parallel_tasks=2,
        ),
    )
    registry.update_task(
        "task-a",
        HocaFleetTask(
            task_id="task-a",
            project_id="project-a",
            status="queued",
            readiness="not_ready",
            metadata={"owned_files": ["src/app.py"]},
            priority=1,
        ),
    )
    registry.update_task(
        "task-b",
        HocaFleetTask(
            task_id="task-b",
            project_id="project-a",
            status="queued",
            readiness="not_ready",
            metadata={"owned_files": ["docs/README.md"]},
            priority=1,
        ),
    )
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=2,
        max_parallel_lanes=2,
        max_agents=10,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    scheduler = FleetScheduler(
        registry=registry, governor=governor, control_root=tmp_path / "control"
    )
    decisions = scheduler.tick()
    launch_decisions = [d for d in decisions if d.decision_type == "launch"]
    assert len(launch_decisions) == 2


def test_scheduler_run_interruption_keeps_state_intact(tmp_path: Path) -> None:
    registry = _registry_with_project_and_tasks(tmp_path)
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=1,
        max_parallel_lanes=1,
        max_agents=4,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    scheduler = FleetScheduler(
        registry=registry, governor=governor, control_root=tmp_path / "control"
    )

    original_tasks = list(registry.list_tasks())
    lock = _resolve_lock_path(tmp_path / "control")

    # simulate interruption during a subsequent loop run.
    def _explode() -> list:
        raise KeyboardInterrupt("intentional test interrupt")

    scheduler.tick = _explode  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_scheduler_loop(
            scheduler=scheduler,
            interval_seconds=0.0,
            max_iterations=1,
            read_only_on_conflict=True,
            control_root=tmp_path / "control",
        )

    assert not lock.exists()
    assert list(registry.list_tasks()) == original_tasks


def test_scheduler_respects_conflicts_and_is_deterministic(tmp_path: Path) -> None:
    registry = _registry_with_project_and_tasks(tmp_path)
    task = registry.get_task("task-b")
    assert task is not None
    registry.update_task(
        "task-b",
        HocaFleetTask(
            task_id="task-b",
            project_id="project-a",
            status="queued",
            readiness="not_ready",
            metadata={"owned_files": ["src/app.py"]},
            priority=1,
        ),
    )
    task_a = HocaFleetTask(
        task_id="task-a",
        project_id="project-a",
        status="queued",
        readiness="not_ready",
        metadata={"owned_files": ["src/app.py"]},
        priority=1,
    )
    registry.update_task("task-a", task_a)

    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=2,
        max_parallel_lanes=2,
        max_agents=10,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    scheduler = FleetScheduler(
        registry=registry, governor=governor, control_root=tmp_path / "control"
    )
    decisions = scheduler.tick()
    statuses = [d.decision_type for d in decisions]
    assert "launch" in statuses
    assert "wait_conflict" in statuses

    # Deterministic relative to queued-state order: second tick should produce same set
    # of deterministic reason-only decisions while no new lane slots free up.
    second = scheduler.tick()
    assert {item.reason for item in decisions if item.decision_type == "wait_conflict"} == {
        item.reason for item in second if item.decision_type == "wait_conflict"
    }


def test_scheduler_process_loop_lock(tmp_path: Path) -> None:
    registry = _registry_with_project_and_tasks(tmp_path)
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=1,
        max_parallel_lanes=1,
        max_agents=4,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    scheduler = FleetScheduler(
        registry=registry, governor=governor, control_root=tmp_path / "control"
    )

    lock = _resolve_lock_path(tmp_path / "control")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    states = run_scheduler_loop(
        scheduler=scheduler,
        interval_seconds=0.0,
        max_iterations=1,
        read_only_on_conflict=True,
        control_root=tmp_path / "control",
    )
    assert states == [(-1, [])]
