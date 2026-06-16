from __future__ import annotations

from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject, HocaResourceBudget
from hoca.resource_governor import ResourceGovernor


def _budget(max_resident_models: int = 2) -> HocaResourceBudget:
    return HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=2,
        max_parallel_tasks=4,
        max_parallel_lanes=4,
        max_agents=10,
        memory_limit_mb=0,
        cpu_limit_percent=0,
        metadata={"max_resident_models": max_resident_models},
    )


def _project() -> HocaProject:
    return HocaProject(
        project_id="p",
        repo_path="/tmp/p",
        default_branch="main",
        max_parallel_tasks=4,
    )


def _task(task_id: str, models: list[str]) -> HocaFleetTask:
    return HocaFleetTask(
        task_id=task_id,
        project_id="p",
        status="queued",
        readiness="ready",
        priority=1,
        metadata={"required_models": models},
    )


def _lane(lane_id: str, task_id: str, models: list[str]) -> HocaLane:
    return HocaLane(
        lane_id=lane_id,
        task_id=task_id,
        project_id="p",
        status="running",
        branch=f"hoca/{lane_id}",
        attempt_number=0,
        metadata={"required_models": models},
    )


def test_two_lanes_sharing_one_model_can_run_under_residency_cap() -> None:
    governor = ResourceGovernor(budget=_budget(max_resident_models=1))

    decision = governor.can_launch(
        project=_project(),
        task=_task("candidate", ["local-coder"]),
        active_lanes=[_lane("lane-1", "running", ["local-coder"])],
        project_running_count=1,
        adapter_id="default",
    )

    assert decision.allowed is True
    assert decision.reason == "capacity_available"


def test_lane_requiring_third_resident_model_waits_with_clear_reason() -> None:
    governor = ResourceGovernor(budget=_budget(max_resident_models=2))

    decision = governor.can_launch(
        project=_project(),
        task=_task("candidate", ["third-model"]),
        active_lanes=[
            _lane("lane-1", "task-1", ["first-model"]),
            _lane("lane-2", "task-2", ["second-model"]),
        ],
        project_running_count=2,
        adapter_id="default",
    )

    assert decision.allowed is False
    assert decision.reason == "model residency cap reached (3/2); added=third-model"


def test_residency_block_does_not_modify_running_lanes() -> None:
    governor = ResourceGovernor(budget=_budget(max_resident_models=1))
    running = [_lane("lane-1", "task-1", ["first-model"])]
    before = [lane.to_dict() for lane in running]

    decision = governor.can_launch(
        project=_project(),
        task=_task("candidate", ["second-model"]),
        active_lanes=running,
        project_running_count=1,
        adapter_id="default",
    )

    assert decision.allowed is False
    assert running[0].to_dict() == before[0]
