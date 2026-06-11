from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from hoca.fleet_contracts import HocaLane, HocaResourceBudget, HocaFleetTask, HocaProject


@dataclass(frozen=True)
class ResourceAssessment:
    allowed: bool
    reason: str
    running_load: float
    projected_load: float


@dataclass(frozen=True)
class ResourceStats:
    load_factor: float
    memory_limit_mb: int
    cpu_limit_percent: int
    free_memory_mb: int | None
    load_avg: float | None


def _metadata_models(metadata: dict | None) -> set[str]:
    if not metadata:
        return set()
    raw = (
        metadata.get("required_models")
        or metadata.get("resident_models")
        or metadata.get("models")
        or metadata.get("model")
        or metadata.get("worker_model")
    )
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    if isinstance(raw, list | tuple | set):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _metadata_int(metadata: dict | None, key: str, default: int = 0) -> int:
    if not metadata:
        return default
    try:
        return int(metadata.get(key, default))
    except (TypeError, ValueError):
        return default


def _memory_mb() -> int | None:
    # Portable best effort; callers treat missing data as advisory-only.
    if hasattr(os, "sysconf") and hasattr(os, "SC_PAGE_SIZE"):
        pages = getattr(os, "sysconf_names", {}).get("SC_PHYS_PAGES")
        if pages is not None:
            try:
                pages = os.sysconf("SC_PHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                return int(pages * page_size / (1024 * 1024))
            except (ValueError, OSError):
                pass
    return None


def _load_avg() -> float | None:
    try:
        load = os.getloadavg()[0]
        return float(load)
    except (OSError, AttributeError):
        return None


class ResourceGovernor:
    def __init__(
        self,
        *,
        budget: HocaResourceBudget,
        adapter_weights: Mapping[str, float] | None = None,
        task_weights: Mapping[str, float] | None = None,
    ) -> None:
        self.budget = budget
        self.adapter_weights = dict(adapter_weights or {})
        self.task_weights = dict(task_weights or {})

    def lane_weight(
        self,
        task: HocaFleetTask,
        *,
        adapter_id: str,
    ) -> float:
        adapter_weight = float(self.adapter_weights.get(adapter_id, 1.0))
        task_weight = float(self.task_weights.get(task.task_id, task.priority))
        return max(adapter_weight, 0.1) * max(task_weight, 0.1)

    @staticmethod
    def task_required_models(task: HocaFleetTask) -> set[str]:
        return _metadata_models(task.metadata)

    @staticmethod
    def lane_required_models(lane: HocaLane) -> set[str]:
        return _metadata_models(lane.metadata)

    def resident_model_limit(self) -> int:
        return _metadata_int(self.budget.metadata, "max_resident_models", 0)

    def resident_models_for_lanes(self, lanes: list[HocaLane]) -> set[str]:
        models: set[str] = set()
        for lane in self.active_lanes(lanes):
            models.update(self.lane_required_models(lane))
        return models

    @staticmethod
    def active_lanes(lanes: list[HocaLane]) -> list[HocaLane]:
        return [
            lane
            for lane in lanes
            if lane.status
            in {"allocated", "starting", "running", "validating", "reviewing", "repairing"}
        ]

    def can_launch(
        self,
        *,
        project: HocaProject,
        task: HocaFleetTask,
        active_lanes: list[HocaLane],
        project_running_count: int,
        adapter_id: str,
    ) -> ResourceAssessment:
        running_lanes = self.active_lanes(active_lanes)
        running_load = sum(self.lane_weight(task=task, adapter_id=adapter_id) for task in [])
        # Conservative placeholder: include all active lanes with a unit cost.
        # Adapter/task-specific overrides apply to the candidate only in this stage.
        running_load += float(len(running_lanes))

        project_budget_limit = min(self.budget.max_parallel_tasks, project.max_parallel_tasks)
        if project_running_count >= project_budget_limit:
            return ResourceAssessment(
                False,
                f"project lane cap reached ({project_running_count}/{project_budget_limit})",
                running_load,
                running_load,
            )

        if len(running_lanes) >= self.budget.max_parallel_lanes:
            return ResourceAssessment(
                False,
                f"fleet lane cap reached ({len(running_lanes)}/{self.budget.max_parallel_lanes})",
                running_load,
                float(len(running_lanes)),
            )

        projected_load = running_load + self.lane_weight(task, adapter_id=adapter_id)
        if self.budget.max_agents > 0 and projected_load > float(self.budget.max_agents):
            return ResourceAssessment(
                False,
                f"agent weight cap reached ({projected_load:.2f}/{self.budget.max_agents})",
                running_load,
                projected_load,
            )

        resident_limit = self.resident_model_limit()
        if resident_limit > 0:
            running_models = self.resident_models_for_lanes(running_lanes)
            candidate_models = self.task_required_models(task)
            projected_models = running_models | candidate_models
            if len(projected_models) > resident_limit:
                added = sorted(projected_models - running_models)
                detail = f"; added={','.join(added)}" if added else ""
                return ResourceAssessment(
                    False,
                    f"model residency cap reached ({len(projected_models)}/{resident_limit}){detail}",
                    running_load,
                    projected_load,
                )

        if self.budget.memory_limit_mb > 0:
            free_memory = _memory_mb()
            if free_memory is not None and free_memory < self.budget.memory_limit_mb:
                return ResourceAssessment(
                    False,
                    f"insufficient free memory ({free_memory}mb<{self.budget.memory_limit_mb}mb)",
                    running_load,
                    projected_load,
                )

        if self.budget.cpu_limit_percent > 0 and self._load_is_breached():
            return ResourceAssessment(
                False,
                f"cpu utilization above {self.budget.cpu_limit_percent}%",
                running_load,
                projected_load,
            )

        return ResourceAssessment(True, "capacity_available", running_load, projected_load)

    def _load_is_breached(self) -> bool:
        load_avg = _load_avg()
        if load_avg is None:
            return False
        cores = os.cpu_count() or 1
        load_pct = load_avg / cores * 100
        return load_pct >= self.budget.cpu_limit_percent

    @staticmethod
    def stats() -> ResourceStats:
        return ResourceStats(
            load_factor=(os.cpu_count() or 1) / 100.0,
            memory_limit_mb=0,
            cpu_limit_percent=0,
            free_memory_mb=_memory_mb(),
            load_avg=_load_avg(),
        )
