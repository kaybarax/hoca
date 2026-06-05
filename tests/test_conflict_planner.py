from __future__ import annotations

from hoca.conflict_planner import (
    DependencyPlan,
    conflict_profile_from_task,
    dependency_launchable,
    detect_dependency_cycle,
    dependency_plan_from_task,
    detect_task_conflicts,
)
from hoca.fleet_contracts import HocaFleetTask


def _task(task_id: str, metadata: dict | None = None, *, project_id: str = "p") -> HocaFleetTask:
    return HocaFleetTask(
        task_id=task_id,
        project_id=project_id,
        status="queued",
        readiness="not_ready",
        metadata=metadata or {},
    )


def test_exact_and_directory_overlaps() -> None:
    left = conflict_profile_from_task(
        _task(
            "a",
            {
                "owned_files": ["src/app.py"],
                "expected_areas": ["src/utils"],
            },
        )
    )
    right = conflict_profile_from_task(_task("b", {"readonly_files": ["src/app.py"]}))
    conflicts = detect_task_conflicts(left, [left, right])
    assert conflicts
    assert not conflicts[0].can_launch
    assert "conflicting_file_areas" in conflicts[0].reason

    directory = conflict_profile_from_task(_task("c", {"owned_files": ["src"]}))
    conflicts = detect_task_conflicts(directory, [directory, left])
    assert not conflicts[0].can_launch


def test_non_overlapping_tasks_can_run_together() -> None:
    left = conflict_profile_from_task(
        _task(
            "a",
            {
                "owned_files": ["src/app.py"],
            },
        )
    )
    right = conflict_profile_from_task(_task("b", {"owned_files": ["docs/README.md"]}))
    conflicts = detect_task_conflicts(left, [left, right])
    assert conflicts == []
def test_high_conflict_serialization_file() -> None:
    left = conflict_profile_from_task(_task("a", {"owned_files": ["package-lock.json"]}))
    right = conflict_profile_from_task(_task("b", {"owned_files": ["README.md"]}))
    conflicts = detect_task_conflicts(left, [left, right])
    assert conflicts
    assert conflicts[0].reason == "package_lock_or_manifest_file_in_use"


def test_conflict_override_records_reason() -> None:
    left = conflict_profile_from_task(_task("a", {"owned_files": ["package-lock.json"]}))
    right = conflict_profile_from_task(_task("b", {"owned_files": ["README.md"]}))
    conflicts = detect_task_conflicts(left, [left, right], override="manual:allow_serialized")
    assert conflicts[0].can_launch
    assert conflicts[0].override_reason == "manual:allow_serialized"


def test_dependency_cycle_and_launchability() -> None:
    plans = [
        DependencyPlan(task_id="a", depends_on=("b",)),
        DependencyPlan(task_id="b", depends_on=("a",)),
    ]
    has_cycle, cycle = detect_dependency_cycle(plans)
    assert has_cycle is True
    assert cycle == ("a", "b", "a")

    tasks = [
        _task("a", {"depends_on": ["b"]}),
        _task("b", {"depends_on": []}),
        _task("c", {"requires_ready_pr": True}),
    ]
    dep_plans = [dependency_plan_from_task(task) for task in tasks]

    can_launch, reason = dependency_launchable("a", dep_plans, completed={"b"}, ready_for_pr=set(), lane_status_map={})
    assert can_launch is True
    assert reason == ""

    can_launch, reason = dependency_launchable("a", dep_plans, completed=set(), ready_for_pr=set(), lane_status_map={})
    assert can_launch is False
    assert reason == "b"

    can_launch, reason = dependency_launchable("c", dep_plans, completed={"a"}, ready_for_pr=set(), lane_status_map={})
    assert can_launch is False
    assert reason == "ready_pr"
