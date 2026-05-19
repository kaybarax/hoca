from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Literal, Self

RiskLevel = Literal["low", "medium", "high"]
AttemptStatus = Literal["completed", "failed", "blocked"]
ReviewVerdict = Literal["LGTM", "fix_required", "blocked"]
FindingSeverity = Literal["critical", "high", "medium", "low", "nit"]
FindingCategory = Literal["correctness", "security", "test", "scope", "maintainability", "style"]
ManagerDecision = Literal["proceed_to_pr", "repair_required", "blocked", "draft_pr_with_blockers"]
NetworkMode = Literal["offline", "package-install", "github-only", "full"]
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
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            enabled=bool(_required(data, "enabled")),
            network_mode=_required(data, "network_mode"),
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

    def safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "models": [model.safe_dict() for model in self.models],
            "roles": self.roles.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        cls._validate_required(data)
        roles = _required(data, "roles")
        if not isinstance(roles, dict):
            raise ValueError("Contract field must be an object: roles")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            models=[HocaModelConfig.from_dict(item) for item in _object_list(data, "models")],
            roles=HocaRoleModelSelection.from_dict(roles),
        )

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
    goal: str
    non_goals: list[str]
    expected_areas: list[str]
    acceptance_criteria: list[str]
    test_commands: list[str]
    risk_level: RiskLevel
    requires_human_approval: bool
    max_rounds: int
    models: HocaRoleModelSelection
    sandbox: HocaSandboxPolicy

    _required_fields: ClassVar[tuple[str, ...]] = (
        "run_id",
        "repo_root",
        "base_branch",
        "task_branch",
        "issue_id",
        "goal",
        "non_goals",
        "expected_areas",
        "acceptance_criteria",
        "test_commands",
        "risk_level",
        "requires_human_approval",
        "max_rounds",
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
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            repo_root=str(_required(data, "repo_root")),
            base_branch=str(_required(data, "base_branch")),
            task_branch=str(_required(data, "task_branch")),
            issue_id=None if data["issue_id"] is None else str(data["issue_id"]),
            goal=str(_required(data, "goal")),
            non_goals=_string_list(data, "non_goals"),
            expected_areas=_string_list(data, "expected_areas"),
            acceptance_criteria=_string_list(data, "acceptance_criteria"),
            test_commands=_string_list(data, "test_commands"),
            risk_level=_required(data, "risk_level"),
            requires_human_approval=bool(_required(data, "requires_human_approval")),
            max_rounds=int(_required(data, "max_rounds")),
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
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            round=int(_required(data, "round")),
            role=str(_required(data, "role")),
            status=_required(data, "status"),
            changed_files=_string_list(data, "changed_files"),
            summary=_string_list(data, "summary"),
            commands_run=_string_list(data, "commands_run"),
            tests_run=_string_list(data, "tests_run"),
            known_risks=_string_list(data, "known_risks"),
            blocked_reason=None
            if data["blocked_reason"] is None
            else str(data["blocked_reason"]),
            artifact_paths=_string_map(data, "artifact_paths"),
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
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            id=str(_required(data, "id")),
            severity=_required(data, "severity"),
            category=_required(data, "category"),
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
        pr_notes = _required(data, "pr_notes")
        if not isinstance(pr_notes, dict):
            raise ValueError("Contract field must be an object: pr_notes")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            round=int(_required(data, "round")),
            role=str(_required(data, "role")),
            verdict=_required(data, "verdict"),
            findings=[
                HocaReviewFinding.from_dict(item) for item in _object_list(data, "findings")
            ],
            pr_notes={str(key): [str(item) for item in value] for key, value in pr_notes.items()},
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
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(_required(data, "run_id")),
            round=int(_required(data, "round")),
            decision=_required(data, "decision"),
            accepted_findings=_string_list(data, "accepted_findings"),
            rejected_findings=_string_list(data, "rejected_findings"),
            downgraded_to_pr_notes=_string_list(data, "downgraded_to_pr_notes"),
            reasoning=_string_list(data, "reasoning"),
            next_worker_brief=None
            if data["next_worker_brief"] is None
            else str(data["next_worker_brief"]),
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
