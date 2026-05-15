from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RiskClassification:
    level: RiskLevel
    reason: str
    auto_mergeable: bool


_HIGH_RISK_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(^|/)auth/", re.IGNORECASE),
    re.compile(r"authenticat|authoriz|login|oauth|session|jwt", re.IGNORECASE),
    re.compile(r"(^|/)security/", re.IGNORECASE),
    re.compile(r"encrypt|decrypt|crypto|tls-|ssl-|(^|/)certs/", re.IGNORECASE),
    re.compile(r"(^|/)payments?/|(^|/)billing/|stripe|invoice|subscription", re.IGNORECASE),
    re.compile(r"(^|/)permissions?/|(^|/)acl/|(^|/)rbac/|access.control", re.IGNORECASE),
    re.compile(r"migrat|db/schema|alembic|flyway|liquibase", re.IGNORECASE),
    re.compile(r"destroy|teardown|drop-|nuke|purge|wipe", re.IGNORECASE),
    re.compile(r"(^|/)infra/|(^|/)terraform/|\.tf$|(^|/)k8s/|(^|/)kubernetes/", re.IGNORECASE),
    re.compile(r"deploy|pipeline|(^|/)ci/|(^|/)cd/", re.IGNORECASE),
    re.compile(r"\.github/workflows/", re.IGNORECASE),
    re.compile(r"Dockerfile|docker-compose", re.IGNORECASE),
]

_HIGH_RISK_DEPENDENCY_FILES: frozenset[str] = frozenset(
    p.lower()
    for p in [
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "go.sum",
        "go.mod",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile.lock",
        "Gemfile.lock",
        "Cargo.lock",
        "composer.lock",
        "poetry.lock",
    ]
)

_LOW_RISK_EXTENSIONS: frozenset[str] = frozenset({
    ".md",
    ".txt",
    ".rst",
    ".adoc",
})

_LOW_RISK_FILENAMES: frozenset[str] = frozenset(
    f.lower()
    for f in [
        "README.md",
        "README.rst",
        "README.txt",
        "README",
        "CHANGELOG.md",
        "CHANGES.md",
        "LICENSE",
        "LICENSE.md",
        "CONTRIBUTING.md",
        "AUTHORS",
        "AUTHORS.md",
        ".gitignore",
        ".editorconfig",
    ]
)

_HIGH_RISK_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bauth\b", re.IGNORECASE),
    re.compile(r"\bpayment", re.IGNORECASE),
    re.compile(r"\bbilling\b", re.IGNORECASE),
    re.compile(r"\bmigrat", re.IGNORECASE),
    re.compile(r"\bsecret", re.IGNORECASE),
    re.compile(r"\bencrypt", re.IGNORECASE),
    re.compile(r"\bpermission", re.IGNORECASE),
    re.compile(r"\binfrastructure\b", re.IGNORECASE),
    re.compile(r"\bdeploy", re.IGNORECASE),
    re.compile(r"\bdelet(e|ion)\b", re.IGNORECASE),
]


def _is_test_only_path(path: str) -> bool:
    lower = path.lower()
    return (
        lower.startswith("tests/")
        or lower.startswith("test/")
        or "/tests/" in lower
        or "/test/" in lower
        or Path(lower).name.startswith("test_")
        or Path(lower).name.endswith("_test.py")
        or Path(lower).name.endswith(".test.ts")
        or Path(lower).name.endswith(".test.js")
        or Path(lower).name.endswith(".spec.ts")
        or Path(lower).name.endswith(".spec.js")
    )


def _is_doc_only_path(path: str) -> bool:
    p = Path(path)
    lower_name = p.name.lower()
    if lower_name in _LOW_RISK_FILENAMES:
        return True
    return p.suffix.lower() in _LOW_RISK_EXTENSIONS


def _is_example_path(path: str) -> bool:
    lower = path.lower()
    return "example" in lower or "sample" in lower or lower.startswith("examples/")


def _path_matches_high_risk(path: str) -> str | None:
    lower_name = Path(path).name.lower()
    if lower_name in _HIGH_RISK_DEPENDENCY_FILES:
        return "dependency file"
    for pattern in _HIGH_RISK_PATH_PATTERNS:
        if pattern.search(path):
            return f"matches high-risk pattern: {pattern.pattern}"
    return None


def classify_paths(paths: list[str]) -> RiskLevel:
    if not paths:
        return RiskLevel.UNKNOWN

    has_high = False
    has_source = False

    for path in paths:
        high_reason = _path_matches_high_risk(path)
        if high_reason:
            has_high = True
            break
        if not _is_doc_only_path(path) and not _is_test_only_path(path) and not _is_example_path(path):
            has_source = True

    if has_high:
        return RiskLevel.HIGH
    if has_source:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def classify_description(description: str) -> RiskLevel:
    if not description.strip():
        return RiskLevel.UNKNOWN
    for pattern in _HIGH_RISK_KEYWORDS:
        if pattern.search(description):
            return RiskLevel.HIGH
    return RiskLevel.UNKNOWN


def classify_task(
    *,
    description: str = "",
    changed_paths: list[str] | None = None,
) -> RiskClassification:
    if not description.strip() and not changed_paths:
        return RiskClassification(
            level=RiskLevel.UNKNOWN,
            reason="No description or file paths provided.",
            auto_mergeable=False,
        )

    desc_risk = classify_description(description) if description.strip() else RiskLevel.UNKNOWN
    path_risk = classify_paths(changed_paths) if changed_paths else RiskLevel.UNKNOWN

    level = _combine_levels(desc_risk, path_risk)

    reason = _build_reason(level, desc_risk, path_risk, changed_paths or [])
    auto_mergeable = _is_auto_mergeable(level)

    return RiskClassification(
        level=level,
        reason=reason,
        auto_mergeable=auto_mergeable,
    )


def _combine_levels(desc_risk: RiskLevel, path_risk: RiskLevel) -> RiskLevel:
    # UNKNOWN means "not enough info" — it should not override a concrete assessment.
    if desc_risk == RiskLevel.UNKNOWN and path_risk != RiskLevel.UNKNOWN:
        return path_risk
    if path_risk == RiskLevel.UNKNOWN and desc_risk != RiskLevel.UNKNOWN:
        return desc_risk
    priority = {RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.UNKNOWN: 1, RiskLevel.LOW: 0}
    if priority[desc_risk] >= priority[path_risk]:
        return desc_risk
    return path_risk


def _is_auto_mergeable(level: RiskLevel) -> bool:
    if level == RiskLevel.HIGH:
        return False
    if level == RiskLevel.UNKNOWN:
        return False
    if level == RiskLevel.MEDIUM:
        return False
    return True


def _build_reason(
    level: RiskLevel,
    desc_risk: RiskLevel,
    path_risk: RiskLevel,
    paths: list[str],
) -> str:
    parts: list[str] = []
    if level == RiskLevel.LOW:
        parts.append("All changed files are documentation, tests, or examples.")
    elif level == RiskLevel.HIGH:
        if desc_risk == RiskLevel.HIGH:
            parts.append("Task description references high-risk domain.")
        if path_risk == RiskLevel.HIGH:
            high_paths = [p for p in paths if _path_matches_high_risk(p)]
            if high_paths:
                parts.append(f"High-risk paths: {', '.join(high_paths[:3])}")
    elif level == RiskLevel.MEDIUM:
        parts.append("Source code changes outside high-risk domains.")
    elif level == RiskLevel.UNKNOWN:
        parts.append("Insufficient information to determine risk.")
    return " ".join(parts) if parts else f"Classified as {level.value}."
