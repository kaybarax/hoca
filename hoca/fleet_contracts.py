from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Self

from hoca.security import is_secret_like_path

FleetTaskStatus = Literal["queued", "ready", "running", "blocked", "cancelled", "completed"]
VALID_FLEET_TASK_STATUSES: frozenset[str] = frozenset(
    ("queued", "ready", "running", "blocked", "cancelled", "completed")
)

FleetLaneStatus = Literal[
    "allocated",
    "starting",
    "running",
    "validating",
    "reviewing",
    "repairing",
    "pr_created",
    "ready_for_human",
    "blocked",
    "failed",
    "cleaned",
]
VALID_FLEET_LANE_STATUSES: frozenset[str] = frozenset(
    (
        "allocated",
        "starting",
        "running",
        "validating",
        "reviewing",
        "repairing",
        "pr_created",
        "ready_for_human",
        "blocked",
        "failed",
        "cleaned",
    )
)

FleetDecisionType = Literal[
    "launch",
    "wait_capacity",
    "wait_dependency",
    "wait_conflict",
    "block",
    "cleanup",
]
VALID_FLEET_DECISION_TYPES: frozenset[str] = frozenset(
    ("launch", "wait_capacity", "wait_dependency", "wait_conflict", "block", "cleanup")
)

FleetReadinessState = Literal["not_ready", "ready", "draft_ready", "blocked"]
VALID_FLEET_READINESS_STATES: frozenset[str] = frozenset(
    ("not_ready", "ready", "draft_ready", "blocked")
)

FleetNotificationStatus = Literal["queued", "sent", "skipped", "failed"]
VALID_FLEET_NOTIFICATION_STATUSES: frozenset[str] = frozenset(
    ("queued", "sent", "skipped", "failed")
)

FleetReviewVerdict = Literal["pass", "needs_work", "blocked"]
VALID_FLEET_REVIEW_VERDICTS: frozenset[str] = frozenset(("pass", "needs_work", "blocked"))
VALID_FINDING_SEVERITIES: frozenset[str] = frozenset(
    ("critical", "high", "medium", "low", "nit")
)
VALID_FINDING_CATEGORIES: frozenset[str] = frozenset(
    (
        "correctness",
        "security",
        "test",
        "scope",
        "maintainability",
        "style",
        "tooling",
        "environment",
    )
)


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _json_loads(raw: str) -> dict[str, Any]:
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Fleet contract JSON must decode to an object")
    return loaded


def _required(data: dict[str, Any], field: str) -> Any:
    try:
        return data[field]
    except KeyError as exc:
        raise ValueError(f"Missing required contract field: {field}") from exc


def _single_line_string(value: Any, field: str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError(f"Contract field must be a single line: {field}")
    return text


def _required_single_line_string(data: dict[str, Any], field: str) -> str:
    value = _single_line_string(_required(data, field), field).strip()
    if not value:
        raise ValueError(f"Contract field must not be empty: {field}")
    return value


def _required_string_list(data: dict[str, Any], field: str) -> list[str]:
    value = _required(data, field)
    if not isinstance(value, list):
        raise ValueError(f"Contract field must be a list: {field}")
    return [_single_line_string(item, field) for item in value]


def _required_int(data: dict[str, Any], field: str, minimum: int = 0) -> int:
    raw = _required(data, field)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Contract field must be an integer: {field}") from exc
    if value < minimum:
        raise ValueError(f"Contract field {field!r} must be >= {minimum}")
    return value


def _required_str_bool(data: dict[str, Any], field: str) -> bool:
    raw = _required(data, field)
    if isinstance(raw, bool):
        return raw
    raise ValueError(f"Contract field must be a boolean: {field}")


def _optional_str(data: dict[str, Any], field: str, *, default: str | None = None) -> str | None:
    value = data.get(field, default)
    if value is None:
        return None
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError(f"Contract field must be a single line: {field}")
    return text


def _string_map(data: dict[str, Any], field: str) -> dict[str, str]:
    value = _required(data, field)
    if not isinstance(value, dict):
        raise ValueError(f"Contract field must be an object: {field}")
    return {str(k): str(v) for k, v in value.items()}


def _optional_string_map(data: dict[str, Any], field: str, *, default: dict[str, str] | None = None) -> dict[str, str]:
    value = data.get(field, default or {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Contract field must be an object: {field}")
    return {str(k): str(v) for k, v in value.items()}


def _validate_non_secret_path(path: str, field: str) -> str:
    if is_secret_like_path(path):
        raise ValueError(f"Contract field path appears secret-like: {field}")
    return path


@dataclass(frozen=True, kw_only=True)
class JsonContract:
    schema_version: int = 1

    _required_fields: ClassVar[tuple[str, ...]] = ()

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def to_json(self) -> str:
        return _json_dumps(self.to_dict())

    @classmethod
    def _validate_required(cls, data: dict[str, Any]) -> None:
        for field in cls._required_fields:
            _required(data, field)


@dataclass(frozen=True)
class HocaProject(JsonContract):
    project_id: str = ""
    repo_path: str = ""
    display_name: str = ""
    default_branch: str = "main"
    max_parallel_tasks: int = 1
    runtime_archive_root: str = ""
    agent_policy: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    is_active: bool = True

    _required_fields: ClassVar[tuple[str, ...]] = ("project_id", "repo_path")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        project_id = _required_single_line_string(data, "project_id")
        repo_path = _required_single_line_string(data, "repo_path")
        if is_secret_like_path(repo_path):
            raise ValueError("repo_path must not be secret-like")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            project_id=project_id,
            repo_path=repo_path,
            display_name=str(data.get("display_name", "")).strip(),
            default_branch=_required_single_line_string(data, "default_branch")
            if "default_branch" in data
            else "main",
            max_parallel_tasks=_required_int({"max_parallel_tasks": data.get("max_parallel_tasks", 1)}, "max_parallel_tasks", minimum=1),
            runtime_archive_root=str(data.get("runtime_archive_root", "")).strip(),
            agent_policy=_optional_string_map(data, "agent_policy", default={}),
            created_at=str(data.get("created_at", "")).strip(),
            updated_at=str(data.get("updated_at", "")).strip(),
            is_active=_required_str_bool(data, "is_active") if "is_active" in data else True,
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaFleetTask(JsonContract):
    task_id: str = ""
    project_id: str = ""
    title: str = ""
    description: str = ""
    issue_id: str | None = None
    goal: str = ""
    status: FleetTaskStatus = "queued"
    readiness: FleetReadinessState = "not_ready"
    dependencies: list[str] | None = None
    lane_ids: list[str] | None = None
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    priority: int = 1
    metadata: dict[str, str] | None = None

    _required_fields: ClassVar[tuple[str, ...]] = (
        "task_id",
        "project_id",
        "status",
        "readiness",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        status = str(_required(data, "status"))
        if status not in VALID_FLEET_TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status!r}")
        readiness = str(_required(data, "readiness"))
        if readiness not in VALID_FLEET_READINESS_STATES:
            raise ValueError(f"Invalid readiness state: {readiness!r}")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            task_id=_required_single_line_string(data, "task_id"),
            project_id=_required_single_line_string(data, "project_id"),
            title=str(data.get("title", "")).strip(),
            description=str(data.get("description", "")).strip(),
            issue_id=_optional_str(data, "issue_id"),
            goal=str(data.get("goal", "")).strip(),
            status=status,  # type: ignore[arg-type]
            readiness=readiness,  # type: ignore[arg-type]
            dependencies=_required_str_list_or_empty(data, "dependencies"),
            lane_ids=_required_str_list_or_empty(data, "lane_ids"),
            created_at=str(data.get("created_at", "")).strip(),
            updated_at=str(data.get("updated_at", "")).strip(),
            completed_at=_optional_str(data, "completed_at"),
            priority=_required_int({"priority": data.get("priority", 1)}, "priority", minimum=0),
            metadata=_optional_string_map(data, "metadata", default={}),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


def _required_str_list_or_empty(data: dict[str, Any], field: str) -> list[str]:
    value = data.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Contract field must be a list: {field}")
    return [_single_line_string(item, field).strip() for item in value]


def _optional_str_or_none(value: Any, field: str) -> str | None:
    if value is None:
        return None
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError(f"Contract field must be a single line: {field}")
    return text


@dataclass(frozen=True)
class HocaTaskDependency(JsonContract):
    task_id: str = ""
    depends_on_task_id: str = ""
    required: bool = True
    reason: str = ""
    created_at: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = ("task_id", "depends_on_task_id")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            task_id=_required_single_line_string(data, "task_id"),
            depends_on_task_id=_required_single_line_string(data, "depends_on_task_id"),
            required=_required_str_bool(data, "required") if "required" in data else True,
            reason=str(data.get("reason", "")).strip(),
            created_at=str(data.get("created_at", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaLane(JsonContract):
    lane_id: str = ""
    task_id: str = ""
    project_id: str = ""
    status: FleetLaneStatus = "allocated"
    worktree_path: str | None = None
    branch: str = ""
    adapter_id: str = ""
    run_dir: str = ""
    session_id: str | None = None
    run_ref: str | None = None
    attempt_number: int = 0
    created_at: str = ""
    started_at: str | None = None
    updated_at: str = ""
    completed_at: str | None = None
    metadata: dict[str, str] | None = None

    _required_fields: ClassVar[tuple[str, ...]] = (
        "lane_id",
        "task_id",
        "project_id",
        "status",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        status = str(_required(data, "status"))
        if status not in VALID_FLEET_LANE_STATUSES:
            raise ValueError(f"Invalid lane status: {status!r}")
        worktree_path = _optional_str(data, "worktree_path")
        if worktree_path:
            _validate_non_secret_path(worktree_path, "worktree_path")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            lane_id=_required_single_line_string(data, "lane_id"),
            task_id=_required_single_line_string(data, "task_id"),
            project_id=_required_single_line_string(data, "project_id"),
            status=status,  # type: ignore[arg-type]
            worktree_path=worktree_path,
            branch=_required_single_line_string(data, "branch") if "branch" in data else "",
            adapter_id=str(data.get("adapter_id", "")).strip(),
            run_dir=str(data.get("run_dir", "")).strip(),
            session_id=_optional_str(data, "session_id"),
            run_ref=_optional_str(data, "run_ref"),
            attempt_number=_required_int(data, "attempt_number", minimum=0),
            created_at=str(data.get("created_at", "")).strip(),
            started_at=_optional_str(data, "started_at"),
            updated_at=str(data.get("updated_at", "")).strip(),
            completed_at=_optional_str(data, "completed_at"),
            metadata=_optional_string_map(data, "metadata", default={}),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaLaneLease(JsonContract):
    lease_id: str = ""
    lane_id: str = ""
    project_id: str = ""
    task_id: str = ""
    branch: str = ""
    base_ref: str = ""
    worktree_path: str = ""
    acquired_at: str = ""
    expires_at: str | None = None
    process_id: int | None = None
    heartbeat_at: str | None = None

    _required_fields: ClassVar[tuple[str, ...]] = (
        "lease_id",
        "lane_id",
        "project_id",
        "task_id",
        "branch",
        "base_ref",
        "worktree_path",
        "acquired_at",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        worktree_path = _required_single_line_string(data, "worktree_path")
        _validate_non_secret_path(worktree_path, "worktree_path")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            lease_id=_required_single_line_string(data, "lease_id"),
            lane_id=_required_single_line_string(data, "lane_id"),
            project_id=_required_single_line_string(data, "project_id"),
            task_id=_required_single_line_string(data, "task_id"),
            branch=_required_single_line_string(data, "branch"),
            base_ref=_required_single_line_string(data, "base_ref"),
            worktree_path=worktree_path,
            acquired_at=_required_single_line_string(data, "acquired_at"),
            expires_at=_optional_str(data, "expires_at"),
            process_id=(
                int(data["process_id"]) if "process_id" in data and data["process_id"] is not None else None
            ),
            heartbeat_at=_optional_str(data, "heartbeat_at"),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaAgentAdapterSpec(JsonContract):
    adapter_id: str = ""
    provider: str = ""
    command_template: str = ""
    command_allowlist: list[str] = field(default_factory=list)
    runtime_home: str | None = None
    max_concurrency: int = 1
    default_for_tasks: list[str] | None = None
    capabilities: list[str] | None = None
    is_active: bool = True
    created_at: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = ("adapter_id", "provider", "command_template")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        runtime_home = _optional_str(data, "runtime_home")
        if runtime_home:
            _validate_non_secret_path(runtime_home, "runtime_home")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            adapter_id=_required_single_line_string(data, "adapter_id"),
            provider=_required_single_line_string(data, "provider"),
            command_template=_required_single_line_string(data, "command_template"),
            command_allowlist=_required_str_list_or_empty(data, "command_allowlist"),
            runtime_home=runtime_home,
            max_concurrency=_required_int(data, "max_concurrency", minimum=1),
            default_for_tasks=_required_str_list_or_empty(data, "default_for_tasks"),
            capabilities=_required_str_list_or_empty(data, "capabilities"),
            is_active=_required_str_bool(data, "is_active") if "is_active" in data else True,
            created_at=str(data.get("created_at", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaAgentSession(JsonContract):
    session_id: str = ""
    lane_id: str = ""
    adapter_id: str = ""
    status: str = ""
    started_at: str = ""
    ended_at: str | None = None
    log_path: str | None = None
    process_id: int | None = None
    metadata: dict[str, str] | None = None

    _required_fields: ClassVar[tuple[str, ...]] = ("session_id", "lane_id", "adapter_id", "status", "started_at")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        log_path = _optional_str(data, "log_path")
        if log_path:
            _validate_non_secret_path(log_path, "log_path")
        status = str(_required(data, "status"))
        if not status:
            raise ValueError("Contract field must not be empty: status")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            session_id=_required_single_line_string(data, "session_id"),
            lane_id=_required_single_line_string(data, "lane_id"),
            adapter_id=_required_single_line_string(data, "adapter_id"),
            status=status,
            started_at=_required_single_line_string(data, "started_at"),
            ended_at=_optional_str(data, "ended_at"),
            log_path=log_path,
            process_id=None if "process_id" not in data else int(data["process_id"]),
            metadata=_optional_string_map(data, "metadata", default={}),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaResourceBudget(JsonContract):
    budget_id: str = ""
    project_id: str | None = None
    max_parallel_projects: int = 1
    max_parallel_tasks: int = 1
    max_parallel_lanes: int = 1
    max_agents: int = 1
    memory_limit_mb: int = 0
    cpu_limit_percent: int = 0
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, str] | None = None

    _required_fields: ClassVar[tuple[str, ...]] = ("budget_id", "max_parallel_projects", "max_parallel_tasks")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            budget_id=_required_single_line_string(data, "budget_id"),
            project_id=_optional_str(data, "project_id"),
            max_parallel_projects=_required_int(data, "max_parallel_projects", minimum=1),
            max_parallel_tasks=_required_int(data, "max_parallel_tasks", minimum=1),
            max_parallel_lanes=_required_int(data, "max_parallel_lanes", minimum=1),
            max_agents=_required_int(data, "max_agents", minimum=1),
            memory_limit_mb=_required_int(data, "memory_limit_mb", minimum=0),
            cpu_limit_percent=_required_int(data, "cpu_limit_percent", minimum=0),
            created_at=str(data.get("created_at", "")).strip(),
            updated_at=str(data.get("updated_at", "")).strip(),
            metadata=_optional_string_map(data, "metadata", default={}),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaSchedulerDecision(JsonContract):
    decision_id: str = ""
    project_id: str = ""
    task_id: str | None = None
    lane_id: str | None = None
    decision_type: FleetDecisionType = "wait_capacity"
    reason: str = ""
    selected_adapter_id: str | None = None
    confidence: float = 1.0
    created_at: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = (
        "decision_id",
        "project_id",
        "decision_type",
        "reason",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        decision_type = str(_required(data, "decision_type"))
        if decision_type not in VALID_FLEET_DECISION_TYPES:
            raise ValueError(f"Invalid decision type: {decision_type!r}")
        confidence = data.get("confidence", 1.0)
        if not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be numeric")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            decision_id=_required_single_line_string(data, "decision_id"),
            project_id=_required_single_line_string(data, "project_id"),
            task_id=_optional_str(data, "task_id"),
            lane_id=_optional_str(data, "lane_id"),
            decision_type=decision_type,  # type: ignore[arg-type]
            reason=_required_single_line_string(data, "reason"),
            selected_adapter_id=_optional_str(data, "selected_adapter_id"),
            confidence=float(confidence),
            created_at=str(data.get("created_at", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaMergeReadiness(JsonContract):
    lane_id: str = ""
    readiness: FleetReadinessState = "not_ready"
    ci_status: str | None = None
    pr_url: str | None = None
    checks: list[str] | None = None
    human_review_required: bool = True
    reason: str | None = None
    checked_at: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = ("lane_id", "readiness")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        readiness = str(_required(data, "readiness"))
        if readiness not in VALID_FLEET_READINESS_STATES:
            raise ValueError(f"Invalid readiness state: {readiness!r}")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            lane_id=_required_single_line_string(data, "lane_id"),
            readiness=readiness,  # type: ignore[arg-type]
            ci_status=_optional_str(data, "ci_status"),
            pr_url=_optional_str(data, "pr_url"),
            checks=_required_str_list_or_empty(data, "checks"),
            human_review_required=_required_str_bool(data, "human_review_required")
            if "human_review_required" in data
            else True,
            reason=_optional_str(data, "reason"),
            checked_at=str(data.get("checked_at", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaReviewSignal(JsonContract):
    signal_id: str = ""
    lane_id: str = ""
    source: str = ""
    verdict: FleetReviewVerdict = "pass"
    summary: str = ""
    details: str | None = None
    review_round: int = 1
    finding_id: str | None = None
    finding_severity: str | None = None
    finding_category: str | None = None
    finding_file: str | None = None
    required_fix: str | None = None
    created_at: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = ("signal_id", "lane_id", "source", "verdict", "created_at")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        verdict = str(_required(data, "verdict"))
        if verdict not in VALID_FLEET_REVIEW_VERDICTS:
            raise ValueError(f"Invalid review verdict: {verdict!r}")

        finding_severity = _optional_str_or_none(data.get("finding_severity"), "finding_severity")
        if finding_severity is not None and finding_severity not in VALID_FINDING_SEVERITIES:
            raise ValueError(f"Invalid finding severity: {finding_severity!r}")

        finding_category = _optional_str_or_none(data.get("finding_category"), "finding_category")
        if finding_category is not None and finding_category not in VALID_FINDING_CATEGORIES:
            raise ValueError(f"Invalid finding category: {finding_category!r}")

        return cls(
            schema_version=int(data.get("schema_version", 1)),
            signal_id=_required_single_line_string(data, "signal_id"),
            lane_id=_required_single_line_string(data, "lane_id"),
            source=_required_single_line_string(data, "source"),
            verdict=verdict,  # type: ignore[arg-type]
            summary=str(data.get("summary", "")).strip(),
            details=_optional_str(data, "details"),
            review_round=_required_int(data, "review_round", minimum=1),
            finding_id=_optional_str_or_none(data.get("finding_id"), "finding_id"),
            finding_severity=finding_severity,
            finding_category=finding_category,
            finding_file=_optional_str_or_none(data.get("finding_file"), "finding_file"),
            required_fix=_optional_str_or_none(data.get("required_fix"), "required_fix"),
            created_at=_required_single_line_string(data, "created_at"),
        )

        

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaNotification(JsonContract):
    notification_id: str = ""
    lane_id: str | None = None
    channel: str = ""
    recipient: str = ""
    message: str = ""
    status: FleetNotificationStatus = "queued"
    created_at: str = ""
    sent_at: str | None = None
    error_message: str | None = None
    payload: dict[str, str] | None = None

    _required_fields: ClassVar[tuple[str, ...]] = (
        "notification_id",
        "channel",
        "recipient",
        "message",
        "status",
        "created_at",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        status = str(_required(data, "status"))
        if status not in VALID_FLEET_NOTIFICATION_STATUSES:
            raise ValueError(f"Invalid notification status: {status!r}")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            notification_id=_required_single_line_string(data, "notification_id"),
            lane_id=_optional_str(data, "lane_id"),
            channel=_required_single_line_string(data, "channel"),
            recipient=_required_single_line_string(data, "recipient"),
            message=_required_single_line_string(data, "message"),
            status=status,  # type: ignore[arg-type]
            created_at=_required_single_line_string(data, "created_at"),
            sent_at=_optional_str(data, "sent_at"),
            error_message=_optional_str(data, "error_message"),
            payload=_optional_string_map(data, "payload", default={}),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaProjectMemoryEntry(JsonContract):
    entry_id: str = ""
    project_id: str = ""
    key: str = ""
    value: dict[str, Any] | None = None
    scope: list[str] | None = None
    created_at: str = ""
    source_task_id: str | None = None
    actor: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = ("entry_id", "project_id", "key", "value")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            entry_id=_required_single_line_string(data, "entry_id"),
            project_id=_required_single_line_string(data, "project_id"),
            key=_required_single_line_string(data, "key"),
            value=_required(data, "value") if isinstance(_required(data, "value"), dict) else None,
            scope=_required_str_list_or_empty(data, "scope"),
            created_at=str(data.get("created_at", "")).strip(),
            source_task_id=_optional_str(data, "source_task_id"),
            actor=str(data.get("actor", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))
