from __future__ import annotations

import contextlib
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from hoca.conflict_planner import (
    DependencyPlan,
    LaneConflictProfile,
    conflict_profile_from_task,
    dependency_plan_from_task,
    dependency_launchable,
    detect_dependency_cycle,
    detect_task_conflicts,
    lanes_conflict,
)
from hoca.control_paths import make_fleet_control_paths
from hoca.fleet_contracts import (
    HocaLane,
    HocaProject,
    HocaSchedulerDecision,
    HocaFleetTask,
)
from hoca.fleet_registry import FleetRegistry
from hoca.resource_governor import ResourceGovernor
from hoca.worktree_pool import generate_lane_branch, slugify


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SchedulerLockError(RuntimeError):
    pass


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class SchedulerLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.held = False

    def acquire(self, *, allow_readonly: bool = False) -> str:
        if self.path.exists():
            try:
                existing = self.path.read_text(encoding="utf-8").strip()
                pid = int(existing)
            except (OSError, ValueError):
                if allow_readonly:
                    return "readonly"
                raise SchedulerLockError("Invalid scheduler lock file")
            if _process_alive(pid):
                if allow_readonly:
                    return "readonly"
                raise SchedulerLockError("Scheduler already running")
            self.path.unlink(missing_ok=True)

        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self.held = True
        return "owned"

    def release(self) -> None:
        if self.held and self.path.exists():
            self.path.unlink()
        self.held = False


class FleetScheduler:
    def __init__(
        self,
        *,
        registry: FleetRegistry,
        governor: ResourceGovernor,
        control_root: Path | None = None,
    ) -> None:
        self.registry = registry
        self.governor = governor
        self.paths = make_fleet_control_paths(override=control_root)

    def _active_projects(self) -> dict[str, HocaProject]:
        return {project.project_id: project for project in self.registry.list_projects() if project.is_active}

    @staticmethod
    def _active_lanes(lanes: list[HocaLane]) -> list[HocaLane]:
        return [lane for lane in lanes if lane.status in {"allocated", "starting", "running", "validating", "reviewing", "repairing", "pr_created", "ready_for_human"}]

    def _next_lane_id(self, task_id: str) -> str:
        existing = [lane.lane_id for lane in self.registry.list_lanes() if lane.task_id == task_id]
        suffix = len(existing) + 1
        return f"{task_id}-lane-{suffix:02d}"

    @staticmethod
    def _sort_tasks(tasks: list[HocaFleetTask]) -> list[HocaFleetTask]:
        return sorted(
            tasks,
            key=lambda task: (-task.priority, task.created_at, task.task_id),
        )

    def refresh_lane_state(self) -> list[HocaLane]:
        return self._active_lanes(self.registry.list_lanes())

    def tick(self) -> list[HocaSchedulerDecision]:
        projects = self._active_projects()
        tasks = [task for task in self.registry.list_tasks() if task.status == "queued"]
        tasks = self._sort_tasks(tasks)
        lanes = self._active_lanes(self.registry.list_lanes())
        active_ids = {lane.task_id for lane in lanes}
        completed_ids = {lane.task_id for lane in self.registry.list_lanes() if lane.status == "cleaned"}
        all_tasks = {task.task_id: task for task in self.registry.list_tasks()}

        lane_profiles: list[LaneConflictProfile] = []
        for lane in lanes:
            linked_task = all_tasks.get(lane.task_id)
            if linked_task is None:
                lane_profiles.append(
                    LaneConflictProfile(task_id=lane.task_id, project_id=lane.project_id)
                )
            else:
                lane_profiles.append(conflict_profile_from_task(linked_task))

        task_profiles = [conflict_profile_from_task(task) for task in tasks]
        dep_plans = [dependency_plan_from_task(task) for task in tasks]

        _, cycle = detect_dependency_cycle(dep_plans)
        if cycle:
            raise RuntimeError(f"Dependency cycle detected: {' -> '.join(cycle)}")

        decisions: list[HocaSchedulerDecision] = []
        for task in tasks:
            project = projects.get(task.project_id)
            if project is None:
                decisions.append(
                    HocaSchedulerDecision(
                        decision_id=f"dec-{task.task_id}-project",
                        project_id=task.project_id,
                        task_id=task.task_id,
                        decision_type="block",
                        reason="unknown_project",
                        created_at=_now_iso(),
                    )
                )
                continue

            task_lane_project_count = len([lane for lane in lanes if lane.project_id == task.project_id])
            launchable, reason = dependency_launchable(
                task.task_id,
                dep_plans,
                completed=completed_ids,
                ready_for_pr={task.task_id} if task.status in {"ready", "running"} else set(),
                lane_status_map={task_id: "running" for task_id in active_ids},
            )
            if not launchable:
                decisions.append(
                    HocaSchedulerDecision(
                        decision_id=f"dec-{task.task_id}-dep",
                        project_id=task.project_id,
                        task_id=task.task_id,
                        decision_type="wait_dependency",
                        reason=reason,
                        created_at=_now_iso(),
                    )
                )
                continue

            task_profile = conflict_profile_from_task(task)
            conflicts = detect_task_conflicts(task_profile, lane_profiles)
            if any(not decision.can_launch for decision in conflicts):
                reasons = sorted(
                    {decision.reason for decision in conflicts if not decision.can_launch}
                )
                decisions.append(
                    HocaSchedulerDecision(
                        decision_id=f"dec-{task.task_id}-conflict",
                        project_id=task.project_id,
                        task_id=task.task_id,
                        decision_type="wait_conflict",
                        reason=",".join(reasons),
                        selected_adapter_id=None,
                        created_at=_now_iso(),
                    )
                )
                continue

            capacity = self.governor.can_launch(
                project=project,
                task=task,
                active_lanes=lanes,
                project_running_count=task_lane_project_count,
                adapter_id=task.metadata.get("adapter_id", "default") if task.metadata else "default",
            )
            if not capacity.allowed:
                decisions.append(
                    HocaSchedulerDecision(
                        decision_id=f"dec-{task.task_id}-capacity",
                        project_id=task.project_id,
                        task_id=task.task_id,
                        decision_type="wait_capacity",
                        reason=capacity.reason,
                        selected_adapter_id=(task.metadata or {}).get("adapter_id", "default"),
                        created_at=_now_iso(),
                    )
                )
                continue

            repo = project.repo_path
            branch = generate_lane_branch(
                Path(repo),
                slugify(task.title or task.goal or task.task_id),
                f"lane-{task.task_id}",
            )
            lane_id = self._next_lane_id(task.task_id)
            lane = HocaLane(
                lane_id=lane_id,
                task_id=task.task_id,
                project_id=task.project_id,
                status="allocated",
                branch=branch,
                adapter_id=(task.metadata or {}).get("adapter_id", "default"),
                run_dir=f"lane/{lane_id}",
                attempt_number=0,
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
            self.registry.create_lane(lane)
            lanes.append(lane)
            lane_profiles.append(task_profile)
            self.registry.update_task(
                task.task_id,
                replace(task, status="running", updated_at=_now_iso()),
            )

            decisions.append(
                HocaSchedulerDecision(
                    decision_id=f"dec-{task.task_id}-launch",
                    project_id=task.project_id,
                    task_id=task.task_id,
                    lane_id=lane_id,
                    decision_type="launch",
                    reason="allocated_lane",
                    selected_adapter_id=lane.adapter_id,
                    confidence=1.0,
                    created_at=_now_iso(),
                )
            )

            if len([lane for lane in lanes if lane.status not in {"completed", "cleaned"}]) >= self.governor.budget.max_parallel_lanes:
                break

        return decisions


def _resolve_lock_path(control_root: Path | None) -> Path:
    return make_fleet_control_paths(override=control_root).resource_state_json.with_name("scheduler.lock")


def run_scheduler_loop(
    *,
    scheduler: FleetScheduler,
    interval_seconds: float,
    max_iterations: int | None = None,
    read_only_on_conflict: bool = True,
    control_root: Path | None = None,
) -> list[tuple[int, list[HocaSchedulerDecision]]]:
    lock = SchedulerLock(_resolve_lock_path(control_root))
    mode = lock.acquire(allow_readonly=read_only_on_conflict)
    if mode == "readonly":
        return [(-1, [])]

    iterations: list[tuple[int, list[HocaSchedulerDecision]]] = []
    try:
        count = 0
        while max_iterations is None or count < max_iterations:
            iterations.append((count, scheduler.tick()))
            count += 1
            if max_iterations is not None and count >= max_iterations:
                break
            time.sleep(interval_seconds)
    finally:
        lock.release()
    return iterations


@contextlib.contextmanager
def read_only_scheduler_context() -> Any:
    # Placeholder used by tests if they need explicit context management.
    yield


def task_summary(task: HocaFleetTask) -> dict[str, Any]:
    return {"id": task.task_id, "project_id": task.project_id, "status": task.status}
