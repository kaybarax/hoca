from __future__ import annotations

from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject, HocaResourceBudget
from hoca.resource_governor import ResourceGovernor


def _task(task_id: str, metadata: dict | None = None, *, weight: float = 1.0) -> HocaFleetTask:
    return HocaFleetTask(
        task_id=task_id,
        project_id="p",
        status="queued",
        readiness="not_ready",
        metadata=metadata or {},
        priority=int(weight),
    )


def _project(*, max_parallel_tasks: int = 2) -> HocaProject:
    return HocaProject(
        project_id="p",
        repo_path="/tmp/p",
        default_branch="main",
        max_parallel_tasks=max_parallel_tasks,
    )


def test_default_budget_is_conservative() -> None:
    budget = HocaResourceBudget(budget_id="default")
    assert budget.max_parallel_projects == 1
    assert budget.max_parallel_tasks == 1
    assert budget.max_parallel_lanes == 1
    assert budget.max_agents == 1


def test_resource_cap_by_project_and_lane_budget() -> None:
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=1,
        max_parallel_lanes=1,
        max_agents=10,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    project = _project(max_parallel_tasks=1)
    task = _task("t1")
    decision = governor.can_launch(
        project=project,
        task=task,
        active_lanes=[
            HocaLane(
                lane_id="l1",
                task_id="t1",
                project_id="p",
                status="running",
                branch="b",
                attempt_number=0,
            )
        ],
        project_running_count=1,
        adapter_id="default",
    )
    assert decision.allowed is False
    assert "project lane cap reached" in decision.reason


def test_resource_can_block_by_agents_weight() -> None:
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=2,
        max_parallel_tasks=4,
        max_parallel_lanes=4,
        max_agents=2,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget, adapter_weights={"gpt": 2.0})
    project = _project(max_parallel_tasks=4)
    task = _task("t1", weight=2.0)

    decision = governor.can_launch(
        project=project,
        task=task,
        active_lanes=[
            HocaLane(
                lane_id="l0",
                task_id="running",
                project_id="p",
                status="running",
                branch="b",
                attempt_number=0,
            ),
        ],
        project_running_count=1,
        adapter_id="gpt",
    )
    assert decision.allowed is False
    assert decision.reason.startswith("agent weight cap reached")


def test_budget_blocking_does_not_modify_running_lanes() -> None:
    budget = HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=1,
        max_parallel_tasks=1,
        max_parallel_lanes=1,
        max_agents=1,
        memory_limit_mb=0,
        cpu_limit_percent=0,
    )
    governor = ResourceGovernor(budget=budget)
    project = _project(max_parallel_tasks=1)
    task = _task("t1", weight=1.0)
    running = [
        HocaLane(
            lane_id="l1",
            task_id="running",
            project_id="p",
            status="running",
            branch="b",
            attempt_number=0,
        )
    ]
    before = list(running)
    decision = governor.can_launch(
        project=project,
        task=task,
        active_lanes=running,
        project_running_count=1,
        adapter_id="default",
    )
    assert decision.allowed is False
    assert running == before
