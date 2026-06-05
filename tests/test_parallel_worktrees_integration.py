from __future__ import annotations

import subprocess
from pathlib import Path

from hoca.fleet_contracts import HocaFleetTask, HocaProject, HocaResourceBudget
from hoca.fleet_registry import FleetRegistry
from hoca.resource_governor import ResourceGovernor
from hoca.scheduler import FleetScheduler
from hoca.worktree_pool import WorktreeLeasePool


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_worktree_leases_are_separate_for_distinct_tasks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    pool = WorktreeLeasePool(control_root=tmp_path / "control")
    lease_a = pool.create_lease(
        lane_id="lane-a",
        project_id="project-a",
        task_id="task-a",
        branch="feat/task-a",
        base_ref="main",
        project_path=repo,
        lease_id="lease-a",
    )
    lease_b = pool.create_lease(
        lane_id="lane-b",
        project_id="project-a",
        task_id="task-b",
        branch="feat/task-b",
        base_ref="main",
        project_path=repo,
        lease_id="lease-b",
    )

    assert lease_a.worktree_path != lease_b.worktree_path
    assert Path(lease_a.worktree_path).exists()
    assert Path(lease_b.worktree_path).exists()


def test_scheduler_launches_non_overlapping_tasks_and_waits_for_overlap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    registry = FleetRegistry(control_root=tmp_path / "control")
    registry.create_project(
        HocaProject(
            project_id="project-a",
            repo_path=str(repo),
            default_branch="main",
            max_parallel_tasks=2,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-a",
            project_id="project-a",
            title="Non-overlap A",
            status="queued",
            readiness="not_ready",
            metadata={"owned_files": ["src/a.py"]},
            priority=1,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-b",
            project_id="project-a",
            title="Non-overlap B",
            status="queued",
            readiness="not_ready",
            metadata={"owned_files": ["docs/b.md"]},
            priority=1,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
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
    scheduler = FleetScheduler(
        registry=registry,
        governor=ResourceGovernor(budget=budget),
        control_root=tmp_path / "control",
    )
    decisions = scheduler.tick()
    launch_decisions = [decision for decision in decisions if decision.decision_type == "launch"]
    assert len(launch_decisions) == 2

    overlapping_registry = FleetRegistry(control_root=tmp_path / "control-overlap")
    overlapping_registry.create_project(
        HocaProject(
            project_id="project-b",
            repo_path=str(repo),
            default_branch="main",
            max_parallel_tasks=2,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    for task_id in ("task-c", "task-d"):
        overlapping_registry.create_task(
            HocaFleetTask(
                task_id=task_id,
                project_id="project-b",
                title=task_id,
                status="queued",
                readiness="not_ready",
                metadata={"owned_files": ["src/shared.py"]},
                priority=1,
                created_at="2026-06-05T00:00:00Z",
                updated_at="2026-06-05T00:00:00Z",
            )
        )
    overlap_scheduler = FleetScheduler(
        registry=overlapping_registry,
        governor=ResourceGovernor(budget=budget),
        control_root=tmp_path / "control-overlap",
    )
    overlap_decisions = overlap_scheduler.tick()
    assert any(decision.decision_type == "launch" for decision in overlap_decisions)
    assert any(decision.decision_type == "wait_conflict" for decision in overlap_decisions)
