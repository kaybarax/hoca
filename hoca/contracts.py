from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Literal, Self

from hoca.security import is_secret_like_path

RiskLevel = Literal["low", "medium", "high"]
VALID_RISK_LEVELS: frozenset[str] = frozenset(("low", "medium", "high"))
VALID_ATTEMPT_STATUSES: frozenset[str] = frozenset(("completed", "failed", "blocked"))
VALID_ATTEMPT_ROLES: frozenset[str] = frozenset(("worker",))
REQUIRED_ATTEMPT_ARTIFACT_PATHS: frozenset[str] = frozenset(
    ("openhands_output", "monitor_result")
)
AttemptStatus = Literal["completed", "failed", "blocked"]
ReviewVerdict = Literal["LGTM", "fix_required", "blocked"]
VALID_REVIEW_VERDICTS: frozenset[str] = frozenset(("LGTM", "fix_required", "blocked"))
VALID_REVIEW_ROLES: frozenset[str] = frozenset(("reviewer",))
FindingSeverity = Literal["critical", "high", "medium", "low", "nit"]
VALID_FINDING_SEVERITIES: frozenset[str] = frozenset(
    ("critical", "high", "medium", "low", "nit")
)
VALID_FINDING_CATEGORIES: frozenset[str] = frozenset(
    ("correctness", "security", "test", "scope", "maintainability", "style", "tooling", "environment")
)
SECURITY_CRITICAL_SEVERITIES: frozenset[str] = frozenset(("critical", "high"))
FindingCategory = Literal[
    "correctness", "security", "test", "scope", "maintainability", "style", "tooling", "environment"
]
ManagerDecision = Literal["proceed_to_pr", "repair_required", "blocked", "draft_pr_with_blockers"]
VALID_MANAGER_DECISIONS: frozenset[str] = frozenset(
    ("proceed_to_pr", "repair_required", "blocked", "draft_pr_with_blockers")
)
NetworkMode = Literal["offline", "package-install", "github-only", "full"]
VALID_NETWORK_MODES: frozenset[str] = frozenset(
    ("offline", "package-install", "github-only", "full")
)
FinalStatus = Literal["completed", "failed", "blocked", "draft_pr_opened", "pr_opened"]


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _json_loads(raw: str) -> dict[str, Any]:
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Contract JSON must decode to an object")
    return loaded


def _required(data: dict[str, Any], field: str) -> Any:
    try:
        return data[field]
    except KeyError as exc:
        raise ValueError(f"Missing required contract field: {field}") from exc


def _string_list(data: dict[str, Any], field: str) -> list[str]:
    value = _required(data, field)
    if not isinstance(value, list):
        raise ValueError(f"Contract field must be a list: {field}")
    return [str(item) for item in value]


def _string_map(data: dict[str, Any], field: str) -> dict[str, str]:
    value = _required(data, field)
    if not isinstance(value, dict):
        raise ValueError(f"Contract field must be an object: {field}")
    return {str(key): str(map_value) for key, map_value in value.items()}


def _single_line_string(value: Any, field: str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError(f"Contract field must be a single line: {field}")
    return text


def _single_line_string_list(data: dict[str, Any], field: str) -> list[str]:
    return [_single_line_string(item, field) for item in _string_list(data, field)]


def _artifact_path_map(data: dict[str, Any], field: str) -> dict[str, str]:
    paths = {
        key: _single_line_string(value, field)
        for key, value in _string_map(data, field).items()
    }
    missing = sorted(REQUIRED_ATTEMPT_ARTIFACT_PATHS - paths.keys())
    if missing:
        raise ValueError(f"Missing required artifact path(s): {', '.join(missing)}")
    for key, path in paths.items():
        if not path:
            raise ValueError(f"Artifact path must not be empty: {key}")
        if is_secret_like_path(path):
            raise ValueError(f"Artifact path must not point to secret-like file: {key}")
    return paths


def _object_list(data: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = _required(data, field)
    if not isinstance(value, list):
        raise ValueError(f"Contract field must be a list: {field}")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Contract list field must contain objects: {field}")
    return value


@dataclass(frozen=True, kw_only=True)
class JsonContract:
    schema_version: int = 1

    _required_fields: ClassVar[tuple[str, ...]] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return _json_dumps(self.to_dict())

    @classmethod
    def _validate_required(cls, data: dict[str, Any]) -> None:
        for field in cls._required_fields:
            _required(data, field)


@dataclass(frozen=True)
class HocaSandboxPolicy(JsonContract):
    enabled: bool = True
    network_mode: NetworkMode = "offline"

    _required_fields: ClassVar[tuple[str, ...]] = ("enabled", "network_mode")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        network_mode = str(_required(data, "network_mode"))
        if network_mode not in VALID_NETWORK_MODES:
            raise ValueError(
                f"network_mode must be one of {sorted(VALID_NETWORK_MODES)}, got: {network_mode!r}"
            )
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            enabled=bool(_required(data, "enabled")),
            network_mode=network_mode,  # type: ignore[arg-type]
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaModelConfig(JsonContract):
    name: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""

    _required_fields: ClassVar[tuple[str, ...]] = ("name", "model")

    @property
    def is_active(self) -> bool:
        return bool(self.name.strip() and self.model.strip())

    def safe_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data["api_key"] = "***" if self.api_key else "(unset)"
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            name=str(_required(data, "name")),
            model=str(_required(data, "model")),
            base_url=str(data.get("base_url", "")),
            api_key=str(data.get("api_key", "")),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaRoleModelSelection(JsonContract):
    manager: str
    worker: str
    reviewer: str
    fallback: str

    _required_fields: ClassVar[tuple[str, ...]] = ("manager", "worker", "reviewer", "fallback")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            manager=str(_required(data, "manager")),
            worker=str(_required(data, "worker")),
            reviewer=str(_required(data, "reviewer")),
            fallback=str(_required(data, "fallback")),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaModelPool(JsonContract):
    models: list[HocaModelConfig]
    roles: HocaRoleModelSelection

    _required_fields: ClassVar[tuple[str, ...]] = ("models", "roles")

    def active_models(self) -> list[HocaModelConfig]:
        return [model for model in self.models if model.is_active]

    def safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "models": [model.safe_dict() for model in self.active_models()],
            "roles": self.roles.to_dict(),
        }

    def to_safe_json(self) -> str:
        return _json_dumps(self.safe_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        from hoca.model_pool import validate_model_pool

        cls._validate_required(data)
        roles = _required(data, "roles")
        if not isinstance(roles, dict):
            raise ValueError("Contract field must be an object: roles")
        pool = cls(
            schema_version=int(data.get("schema_version", 1)),
            models=[HocaModelConfig.from_dict(item) for item in _object_list(data, "models")],
            roles=HocaRoleModelSelection.from_dict(roles),
        )
        validate_model_pool(pool)
        return pool

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaTaskSpec(JsonContract):
    run_id: str
    repo_root: str
    base_branch: str
    task_branch: str
    issue_id: str | None
    raw_request: str
    goal: str
    non_goals: list[str]
    expected_areas: list[str]
    acceptance_criteria: list[str]
    test_commands: list[str]
    risk_level: RiskLevel
    requires_human_approval: bool
    max_total_rounds: int
    models: HocaRoleModelSelection
    sandbox: HocaSandboxPolicy

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "repo_root",
        "base_branch",
        "task_branch",
        "issue_id",
        "raw_request",
        "goal",
        "non_goals",
        "expected_areas",
        "acceptance_criteria",
        "test_commands",
        "risk_level",
        "requires_human_approval",
        "models",
        "sandbox",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        models = _required(data, "models")
        sandbox = _required(data, "sandbox")
        if not isinstance(models, dict):
            raise ValueError("Contract field must be an object: models")
        if not isinstance(sandbox, dict):
            raise ValueError("Contract field must be an object: sandbox")
        risk = _required(data, "risk_level")
        if risk not in VALID_RISK_LEVELS:
            raise ValueError(
                f"risk_level must be one of {sorted(VALID_RISK_LEVELS)}, got: {risk!r}"
            )
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            repo_root=str(_required(data, "repo_root")),
            base_branch=str(_required(data, "base_branch")),
            task_branch=str(_required(data, "task_branch")),
            issue_id=None if data["issue_id"] is None else str(data["issue_id"]),
            raw_request=str(_required(data, "raw_request")),
            goal=str(_required(data, "goal")),
            non_goals=_string_list(data, "non_goals"),
            expected_areas=_string_list(data, "expected_areas"),
            acceptance_criteria=_string_list(data, "acceptance_criteria"),
            test_commands=_string_list(data, "test_commands"),
            risk_level=risk,
            requires_human_approval=bool(_required(data, "requires_human_approval")),
            max_total_rounds=int(data.get("max_total_rounds", 3)),
            models=HocaRoleModelSelection.from_dict(models),
            sandbox=HocaSandboxPolicy.from_dict(sandbox),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaAttemptReport(JsonContract):
    run_id: str
    round: int
    role: str
    status: AttemptStatus
    changed_files: list[str]
    summary: list[str]
    commands_run: list[str]
    tests_run: list[str]
    known_risks: list[str]
    blocked_reason: str | None
    artifact_paths: dict[str, str]

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "round",
        "role",
        "status",
        "changed_files",
        "summary",
        "commands_run",
        "tests_run",
        "known_risks",
        "blocked_reason",
        "artifact_paths",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        round_number = int(_required(data, "round"))
        if round_number < 1:
            raise ValueError("round must be greater than or equal to 1")
        role = _single_line_string(_required(data, "role"), "role")
        if role not in VALID_ATTEMPT_ROLES:
            raise ValueError(
                f"role must be one of {sorted(VALID_ATTEMPT_ROLES)}, got: {role!r}"
            )
        status = _single_line_string(_required(data, "status"), "status")
        if status not in VALID_ATTEMPT_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_ATTEMPT_STATUSES)}, got: {status!r}"
            )
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=_single_line_string(_required(data, "run_id"), "run_id"),
            round=round_number,
            role=role,
            status=status,
            changed_files=_single_line_string_list(data, "changed_files"),
            summary=_single_line_string_list(data, "summary"),
            commands_run=_single_line_string_list(data, "commands_run"),
            tests_run=_single_line_string_list(data, "tests_run"),
            known_risks=_single_line_string_list(data, "known_risks"),
            blocked_reason=None
            if data["blocked_reason"] is None
            else _single_line_string(data["blocked_reason"], "blocked_reason"),
            artifact_paths=_artifact_path_map(data, "artifact_paths"),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaReviewFinding(JsonContract):
    id: str
    severity: FindingSeverity
    category: FindingCategory
    file: str | None
    summary: str
    required_fix: str | None

    _required_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "severity",
        "category",
        "file",
        "summary",
        "required_fix",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        severity = _required(data, "severity")
        if severity not in VALID_FINDING_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(VALID_FINDING_SEVERITIES)}, got: {severity!r}"
            )
        category = _required(data, "category")
        if category not in VALID_FINDING_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(VALID_FINDING_CATEGORIES)}, got: {category!r}"
            )
        if category == "security" and severity in ("low", "nit"):
            raise ValueError(
                f"Security findings must have severity critical, high, or medium — "
                f"got {severity!r}. Use a non-security category for low-priority observations."
            )
        if category == "correctness" and severity == "nit":
            raise ValueError(
                "Correctness findings cannot have severity 'nit'. "
                "Use 'low' or higher, or a different category."
            )
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            id=str(_required(data, "id")),
            severity=severity,
            category=category,
            file=None if data["file"] is None else str(data["file"]),
            summary=str(_required(data, "summary")),
            required_fix=None if data["required_fix"] is None else str(data["required_fix"]),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaReviewReport(JsonContract):
    run_id: str
    round: int
    role: str
    verdict: ReviewVerdict
    findings: list[HocaReviewFinding]
    pr_notes: dict[str, list[str]]

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "round",
        "role",
        "verdict",
        "findings",
        "pr_notes",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        round_number = int(_required(data, "round"))
        if round_number < 1:
            raise ValueError("round must be greater than or equal to 1")
        role = str(_required(data, "role"))
        if role not in VALID_REVIEW_ROLES:
            raise ValueError(
                f"role must be one of {sorted(VALID_REVIEW_ROLES)}, got: {role!r}"
            )
        verdict = _required(data, "verdict")
        if verdict not in VALID_REVIEW_VERDICTS:
            raise ValueError(
                f"verdict must be one of {sorted(VALID_REVIEW_VERDICTS)}, got: {verdict!r}"
            )
        pr_notes = _required(data, "pr_notes")
        if not isinstance(pr_notes, dict):
            raise ValueError("Contract field must be an object: pr_notes")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            round=round_number,
            role=role,
            verdict=verdict,
            findings=[
                HocaReviewFinding.from_dict(item) for item in _object_list(data, "findings")
            ],
            pr_notes={str(key): [str(item) for item in value] for key, value in pr_notes.items()},
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaValidationReport(JsonContract):
    run_id: str
    round: int
    tests_passed: bool
    test_failure_type: str | None
    git_status: list[str]
    changed_files: list[str]
    secret_scan_clean: bool
    monitor_clean: bool
    monitor_stop_reason: str | None
    hard_blockers: list[str]
    scope_risk: bool
    staging_risk: bool
    artifact_paths: dict[str, str]

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "round",
        "tests_passed",
        "test_failure_type",
        "git_status",
        "changed_files",
        "secret_scan_clean",
        "monitor_clean",
        "monitor_stop_reason",
        "hard_blockers",
        "scope_risk",
        "staging_risk",
        "artifact_paths",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        round_number = int(_required(data, "round"))
        if round_number < 1:
            raise ValueError("round must be greater than or equal to 1")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=_single_line_string(_required(data, "run_id"), "run_id"),
            round=round_number,
            tests_passed=bool(_required(data, "tests_passed")),
            test_failure_type=None
            if data["test_failure_type"] is None
            else _single_line_string(data["test_failure_type"], "test_failure_type"),
            git_status=_single_line_string_list(data, "git_status"),
            changed_files=_single_line_string_list(data, "changed_files"),
            secret_scan_clean=bool(_required(data, "secret_scan_clean")),
            monitor_clean=bool(_required(data, "monitor_clean")),
            monitor_stop_reason=None
            if data["monitor_stop_reason"] is None
            else _single_line_string(data["monitor_stop_reason"], "monitor_stop_reason"),
            hard_blockers=_single_line_string_list(data, "hard_blockers"),
            scope_risk=bool(_required(data, "scope_risk")),
            staging_risk=bool(_required(data, "staging_risk")),
            artifact_paths=_string_map(data, "artifact_paths"),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaManagerDecision(JsonContract):
    run_id: str
    round: int
    decision: ManagerDecision
    accepted_findings: list[str]
    rejected_findings: list[str]
    downgraded_to_pr_notes: list[str]
    reasoning: list[str]
    next_worker_brief: str | None
    human_attention_required: bool

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "round",
        "decision",
        "accepted_findings",
        "rejected_findings",
        "downgraded_to_pr_notes",
        "reasoning",
        "next_worker_brief",
        "human_attention_required",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        round_number = int(_required(data, "round"))
        if round_number < 1:
            raise ValueError("round must be greater than or equal to 1")
        decision = _required(data, "decision")
        if decision not in VALID_MANAGER_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(VALID_MANAGER_DECISIONS)}, got: {decision!r}"
            )
        next_worker_brief = (
            None if data["next_worker_brief"] is None else str(data["next_worker_brief"])
        )
        if decision == "repair_required" and not next_worker_brief:
            raise ValueError(
                "next_worker_brief is required when decision is 'repair_required'"
            )
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            round=round_number,
            decision=decision,
            accepted_findings=_string_list(data, "accepted_findings"),
            rejected_findings=_string_list(data, "rejected_findings"),
            downgraded_to_pr_notes=_string_list(data, "downgraded_to_pr_notes"),
            reasoning=_string_list(data, "reasoning"),
            next_worker_brief=next_worker_brief,
            human_attention_required=bool(_required(data, "human_attention_required")),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))


@dataclass(frozen=True)
class HocaRunFinalState(JsonContract):
    run_id: str
    status: FinalStatus
    summary: list[str]
    changed_files: list[str]
    tests_run: list[str]
    attempt_reports: list[str]
    review_reports: list[str]
    manager_decisions: list[str]
    pr_url: str | None
    completed_at: str | None
    blocked_reason: str | None

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "status",
        "summary",
        "changed_files",
        "tests_run",
        "attempt_reports",
        "review_reports",
        "manager_decisions",
        "pr_url",
        "completed_at",
        "blocked_reason",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            status=_required(data, "status"),
            summary=_string_list(data, "summary"),
            changed_files=_string_list(data, "changed_files"),
            tests_run=_string_list(data, "tests_run"),
            attempt_reports=_string_list(data, "attempt_reports"),
            review_reports=_string_list(data, "review_reports"),
            manager_decisions=_string_list(data, "manager_decisions"),
            pr_url=None if data["pr_url"] is None else str(data["pr_url"]),
            completed_at=None if data["completed_at"] is None else str(data["completed_at"]),
            blocked_reason=None
            if data["blocked_reason"] is None
            else str(data["blocked_reason"]),
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        return cls.from_dict(_json_loads(raw))
