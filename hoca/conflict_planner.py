from __future__ import annotations

from dataclasses import dataclass, replace

from hoca.fleet_contracts import HocaFleetTask


HOCALowRiskSerialFiles = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "requirements.txt",
        "go.mod",
        "go.sum",
        "Cargo.lock",
        "Gemfile.lock",
    }
)

HIGH_CONFLICT_PREFIXES = (
    "schema",
    "migrations",
    "generated",
    "clients",
    "api",
)


@dataclass(frozen=True)
class LaneConflictProfile:
    task_id: str
    project_id: str
    expected_areas: tuple[str, ...] = ()
    owned_files: tuple[str, ...] = ()
    readonly_files: tuple[str, ...] = ()
    generated_files: tuple[str, ...] = ()
    conflict_group: str = ""
    requires_ready_pr: bool = False
    priority: int = 1

    @property
    def all_files(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self.expected_areas,
                    *self.owned_files,
                    *self.readonly_files,
                    *self.generated_files,
                )
            )
        )


@dataclass(frozen=True)
class DependencyPlan:
    task_id: str
    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    same_project_after: tuple[str, ...] = ()
    requires_ready_pr: bool = False


@dataclass(frozen=True)
class ConflictDecision:
    can_launch: bool
    reason: str
    overlap_paths: tuple[str, ...] = ()
    override_reason: str | None = None
    release_risk: str = ""
    escalation_reason: str = ""


def _from_metadata(task: HocaFleetTask, field: str, *, as_set: bool = False) -> list[str] | bool:
    metadata = task.metadata or {}
    if field not in metadata:
        return [] if as_set else False
    value = metadata[field]
    if field == "requires_ready_pr":
        return bool(value)
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return [] if as_set else False


def conflict_profile_from_task(task: HocaFleetTask) -> LaneConflictProfile:
    return LaneConflictProfile(
        task_id=task.task_id,
        project_id=task.project_id,
        expected_areas=tuple(
            item.strip("/")
            for item in _from_metadata(task, "expected_areas", as_set=True)
            if str(item).strip()
        ),
        owned_files=tuple(_from_metadata(task, "owned_files", as_set=True)),
        readonly_files=tuple(_from_metadata(task, "readonly_files", as_set=True)),
        generated_files=tuple(_from_metadata(task, "generated_files", as_set=True)),
        conflict_group=str(task.metadata.get("conflict_group", "") if task.metadata else ""),
        requires_ready_pr=bool(_from_metadata(task, "requires_ready_pr")),
        priority=task.priority,
    )


def dependency_plan_from_task(task: HocaFleetTask) -> DependencyPlan:
    return DependencyPlan(
        task_id=task.task_id,
        depends_on=tuple(_from_metadata(task, "depends_on", as_set=True)),
        blocks=tuple(_from_metadata(task, "blocks", as_set=True)),
        same_project_after=tuple(_from_metadata(task, "same_project_after", as_set=True)),
        requires_ready_pr=bool(_from_metadata(task, "requires_ready_pr")),
    )


def _is_prefix(parent: str, child: str) -> bool:
    parent = parent.rstrip("/")
    child = child.rstrip("/")
    if not parent or not child:
        return False
    return child == parent or child.startswith(parent + "/")


def _normalise_area(area: str) -> str:
    return area.strip("/").lstrip(".")


def _normalize_files(profile: LaneConflictProfile) -> tuple[str, ...]:
    return tuple(sorted({_normalise_area(path) for path in profile.all_files if path}))


def detect_path_overlaps(
    left: LaneConflictProfile, right: LaneConflictProfile
) -> tuple[bool, tuple[str, ...]]:
    left_files = _normalize_files(left)
    right_files = _normalize_files(right)
    overlap: list[str] = []
    for left_path in left_files:
        for right_path in right_files:
            if _is_prefix(left_path, right_path) or _is_prefix(right_path, left_path):
                overlap.append(f"{left_path} <-> {right_path}")
    return (bool(overlap), tuple(overlap))


def _is_serialization_file(path: str) -> bool:
    return _normalise_area(path) in HOCALowRiskSerialFiles or _normalise_area(path).startswith(
        tuple(HIGH_CONFLICT_PREFIXES)
    )


def _release_risk_for_serialization_file(path: str) -> tuple[str, str]:
    normalized = _normalise_area(path)
    if normalized in HOCALowRiskSerialFiles:
        return ("high", "dependency_manifest_or_lockfile")
    if normalized.startswith(tuple(HIGH_CONFLICT_PREFIXES)):
        return ("high", "shared_contract_or_generated_surface")
    return ("", "")


def release_risk_for_profile(profile: LaneConflictProfile) -> tuple[str, str]:
    for path in profile.all_files:
        release_risk, escalation_reason = _release_risk_for_serialization_file(path)
        if release_risk:
            return (release_risk, escalation_reason)
    return ("", "")


def _is_high_conflict_due_to_manifests(profile: LaneConflictProfile) -> bool:
    return any(_is_serialization_file(path) for path in profile.all_files)


def lanes_conflict(
    left: LaneConflictProfile,
    right: LaneConflictProfile,
    *,
    override: str | None = None,
) -> ConflictDecision:
    overlap, paths = detect_path_overlaps(left, right)
    if _is_high_conflict_due_to_manifests(left) or _is_high_conflict_due_to_manifests(right):
        release_risk, escalation_reason = release_risk_for_profile(left)
        if not release_risk:
            release_risk, escalation_reason = release_risk_for_profile(right)
        if left.task_id == right.task_id:
            return ConflictDecision(True, "same-task")
        if override is not None:
            return ConflictDecision(
                True,
                "allowed_by_override",
                override_reason=override,
                release_risk=release_risk,
                escalation_reason=escalation_reason,
            )
        return ConflictDecision(
            False,
            "package_lock_or_manifest_file_in_use",
            paths,
            release_risk=release_risk,
            escalation_reason=escalation_reason,
        )

    if overlap:
        if override is not None:
            return ConflictDecision(True, "allowed_by_override", paths, override)
        return ConflictDecision(False, "conflicting_file_areas", paths)
    return ConflictDecision(True, "no_conflict")


def detect_task_conflicts(
    target: LaneConflictProfile,
    existing: list[LaneConflictProfile],
    *,
    override: str | None = None,
) -> list[ConflictDecision]:
    decisions: list[ConflictDecision] = []
    for candidate in existing:
        if candidate.task_id == target.task_id:
            continue
        if candidate.project_id != target.project_id:
            continue
        decision = lanes_conflict(target, candidate, override=override)
        if not decision.can_launch or override is not None:
            decisions.append(decision)
    return decisions


def _dependency_edges(tasks: list[DependencyPlan]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for task in tasks:
        edges.setdefault(task.task_id, set())
        for depends in task.depends_on:
            edges[task.task_id].add(depends)
        for item in task.same_project_after:
            edges[task.task_id].add(item)
        for item in task.blocks:
            edges[task.task_id].add(item)
    return edges


def detect_dependency_cycle(tasks: list[DependencyPlan]) -> tuple[bool, tuple[str, ...]]:
    edges = _dependency_edges(tasks)
    state: dict[str, str] = {}
    order: list[str] = []

    def visit(node: str) -> bool:
        if state.get(node) == "visiting":
            cycle_index = order.index(node)
            raise RuntimeError(
                "Circular dependency detected: " + " -> ".join(order[cycle_index:] + [node])
            )
        if state.get(node) == "done":
            return False
        state[node] = "visiting"
        order.append(node)
        for edge in edges.get(node, set()):
            if edge in edges:
                visit(edge)
        state[node] = "done"
        order.pop()
        return False

    for task_id in sorted(edges):
        try:
            visit(task_id)
        except RuntimeError as exc:
            return True, tuple(str(exc).split(":", 1)[1].strip().split(" -> "))
    return False, ()


def dependency_launchable(
    target_id: str,
    plans: list[DependencyPlan],
    *,
    completed: set[str],
    ready_for_pr: set[str],
    lane_status_map: dict[str, str],
) -> tuple[bool, str]:
    by_task = {plan.task_id: plan for plan in plans}
    if target_id in completed:
        return True, "already_completed"
    target_plan = by_task.get(target_id)
    if target_plan is None:
        return False, "task_not_found"

    blockers: list[str] = []
    for dependency in target_plan.depends_on:
        if dependency not in completed:
            blockers.append(dependency)
    for dependency in target_plan.same_project_after:
        if dependency not in completed:
            blockers.append(dependency)

    if target_plan.requires_ready_pr and target_id not in ready_for_pr:
        blockers.append("ready_pr")

    for blocker in target_plan.blocks:
        for _, target in by_task.items():
            if blocker in target.blocks and blocker == target_id:
                continue
        if blocker not in completed and lane_status_map.get(blocker) not in {
            "completed",
            "cleaned",
        }:
            blockers.append(blocker)

    if blockers:
        return False, ", ".join(sorted(dict.fromkeys(blockers)))
    return True, ""


def apply_dependency_overrides(
    plan: list[DependencyPlan], *, override: dict[str, str] | None = None
) -> list[DependencyPlan]:
    if not override:
        return plan
    updated = []
    for item in plan:
        if item.task_id not in override:
            updated.append(item)
            continue
        if override[item.task_id] == "allow_conflict":
            updated.append(replace(item, requires_ready_pr=False))
            continue
        updated.append(item)
    return updated
