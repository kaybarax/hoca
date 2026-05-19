from __future__ import annotations

import re
from pathlib import Path

import pytest

from hoca.contracts import (
    HocaAttemptReport,
    HocaManagerDecision,
    HocaReviewReport,
    HocaSandboxPolicy,
    HocaTaskSpec,
    HocaValidationReport,
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
