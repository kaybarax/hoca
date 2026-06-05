from __future__ import annotations

import re
from pathlib import Path

import pytest

from hoca.contracts import (
    HocaAttemptReport,
    HocaManagerDecision,
    HocaNotification,
    HocaResourceBudget,
    HocaReviewReport,
    HocaSandboxPolicy,
    HocaTaskSpec,
    HocaValidationReport,
)
from hoca.fleet_contracts import (
    HocaAgentAdapterSpec,
    HocaAgentSession,
    HocaFleetTask,
    HocaLane,
    HocaLaneLease,
    HocaMergeReadiness,
    HocaProject,
    HocaProjectMemoryEntry,
    HocaReviewSignal,
    HocaSchedulerDecision,
    HocaTaskDependency,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "templates"

CONTRACT_TEMPLATES: tuple[tuple[type, str, tuple[str, ...]], ...] = (
    (
        HocaTaskSpec,
        "HocaTaskSpec.yaml",
        (
            *HocaTaskSpec._required_fields,
            "schema_version",
            "max_total_rounds",
        ),
    ),
    (
        HocaAttemptReport,
        "HocaAttemptReport.yaml",
        (*HocaAttemptReport._required_fields, "schema_version"),
    ),
    (
        HocaReviewReport,
        "HocaReviewReport.yaml",
        (
            *HocaReviewReport._required_fields,
            "schema_version",
            "id",
            "severity",
            "category",
            "required_fix",
            "known_followups",
        ),
    ),
    (
        HocaManagerDecision,
        "HocaManagerDecision.yaml",
        (*HocaManagerDecision._required_fields, "schema_version"),
    ),
    (
        HocaValidationReport,
        "HocaValidationReport.yaml",
        (*HocaValidationReport._required_fields, "schema_version"),
    ),
    (
        HocaSandboxPolicy,
        "HocaSandboxPolicy.yaml",
        (*HocaSandboxPolicy._required_fields, "schema_version"),
    ),
    (
        HocaProject,
        "HocaProject.yaml",
        (*HocaProject._required_fields, "schema_version", "is_active"),
    ),
    (
        HocaFleetTask,
        "HocaFleetTask.yaml",
        (*HocaFleetTask._required_fields, "schema_version"),
    ),
    (
        HocaTaskDependency,
        "HocaTaskDependency.yaml",
        (*HocaTaskDependency._required_fields, "schema_version", "reason"),
    ),
    (
        HocaLane,
        "HocaLane.yaml",
        (*HocaLane._required_fields, "schema_version", "attempt_number"),
    ),
    (
        HocaLaneLease,
        "HocaLaneLease.yaml",
        (*HocaLaneLease._required_fields, "schema_version", "heartbeat_at", "process_id"),
    ),
    (
        HocaAgentAdapterSpec,
        "HocaAgentAdapterSpec.yaml",
        (
            *HocaAgentAdapterSpec._required_fields,
            "schema_version",
            "max_concurrency",
            "default_for_tasks",
        ),
    ),
    (
        HocaAgentSession,
        "HocaAgentSession.yaml",
        (*HocaAgentSession._required_fields, "schema_version", "process_id"),
    ),
    (
        HocaResourceBudget,
        "HocaResourceBudget.yaml",
        (*HocaResourceBudget._required_fields, "schema_version", "max_parallel_lanes"),
    ),
    (
        HocaSchedulerDecision,
        "HocaSchedulerDecision.yaml",
        (*HocaSchedulerDecision._required_fields, "schema_version", "confidence"),
    ),
    (
        HocaMergeReadiness,
        "HocaMergeReadiness.yaml",
        (*HocaMergeReadiness._required_fields, "schema_version", "checks"),
    ),
    (
        HocaReviewSignal,
        "HocaReviewSignal.yaml",
        (*HocaReviewSignal._required_fields, "schema_version", "summary"),
    ),
    (
        HocaNotification,
        "HocaNotification.yaml",
        (*HocaNotification._required_fields, "schema_version"),
    ),
    (
        HocaProjectMemoryEntry,
        "HocaProjectMemoryEntry.yaml",
        (*HocaProjectMemoryEntry._required_fields, "schema_version", "created_at"),
    ),
)


def _strip_yaml_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(lines)


def _field_pattern(field: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*-?\s*{re.escape(field)}:\s", re.MULTILINE)


@pytest.mark.parametrize(("contract_cls", "filename", "fields"), CONTRACT_TEMPLATES)
def test_template_file_exists(
    contract_cls: type,
    filename: str,
    fields: tuple[str, ...],
) -> None:
    path = TEMPLATES_DIR / filename
    assert path.is_file(), f"Missing template for {contract_cls.__name__}: {path}"


@pytest.mark.parametrize(("contract_cls", "filename", "fields"), CONTRACT_TEMPLATES)
def test_template_documents_contract_fields(
    contract_cls: type,
    filename: str,
    fields: tuple[str, ...],
) -> None:
    content = (TEMPLATES_DIR / filename).read_text(encoding="utf-8")
    body = _strip_yaml_comments(content)

    for field in fields:
        assert _field_pattern(field).search(body), (
            f"{filename} is missing documented field {field!r} for {contract_cls.__name__}"
        )


@pytest.mark.parametrize(("contract_cls", "filename", "fields"), CONTRACT_TEMPLATES)
def test_template_avoids_secret_like_paths(
    contract_cls: type,
    filename: str,
    fields: tuple[str, ...],
) -> None:
    content = (TEMPLATES_DIR / filename).read_text(encoding="utf-8").lower()

    forbidden_fragments = (
        "/users/",
        "/home/",
        "api_key:",
        "github_token",
        ".env",
        "id_rsa",
        "credentials.json",
    )
    for fragment in forbidden_fragments:
        assert fragment not in content, (
            f"{filename} must not include secret-like example content: {fragment!r}"
        )
