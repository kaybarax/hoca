"""Deterministic hard-blocker rules for HOCA manager arbitration.

Hard blockers stop a run from proceeding to a normal ready PR. Some validation
blockers are repairable in earlier rounds; absolute validation blockers and
review finding hard blockers cannot be bypassed at the round cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hoca.contracts import (
    HocaReviewFinding,
    SECURITY_CRITICAL_SEVERITIES,
)

HardBlockerId = Literal[
    "secret_file_change",
    "secret_access_attempt",
    "unsafe_filesystem_access",
    "unreviewed_changed_files",
    "unaccounted_staged_files",
    "test_failure",
    "severe_correctness_finding",
    "security_regression_finding",
    "dirty_unrelated_work",
    "detached_head",
    "missing_pr_credentials",
    "scope_risk",
    "staging_risk",
]

BlockerSource = Literal["validation", "finding"]
BlockerDisposition = Literal["absolute", "repairable", "finding"]

# Validation blockers that cannot be resolved by another worker repair round.
ABSOLUTE_VALIDATION_BLOCKERS: frozenset[str] = frozenset(
    (
        "secret_file_change",
        "secret_access_attempt",
        "unsafe_filesystem_access",
        "missing_pr_credentials",
        "detached_head",
    )
)

# Validation blockers that may be repairable before the round cap.
REPAIRABLE_VALIDATION_BLOCKERS: frozenset[str] = frozenset(
    (
        "test_failure",
        "unreviewed_changed_files",
        "unaccounted_staged_files",
        "dirty_unrelated_work",
        "scope_risk",
        "staging_risk",
    )
)

ALL_VALIDATION_BLOCKER_IDS: frozenset[str] = (
    ABSOLUTE_VALIDATION_BLOCKERS | REPAIRABLE_VALIDATION_BLOCKERS
)

ALL_FINDING_HARD_BLOCKER_IDS: frozenset[str] = frozenset(
    (
        "severe_correctness_finding",
        "security_regression_finding",
    )
)

ALL_HARD_BLOCKER_IDS: frozenset[str] = (
    ALL_VALIDATION_BLOCKER_IDS | ALL_FINDING_HARD_BLOCKER_IDS
)

# Monitor stop reasons mapped to validation hard-blocker IDs.
MONITOR_STOP_REASON_TO_BLOCKER: dict[str, str] = {
    "secret_access": "secret_access_attempt",
    "unrelated_directory": "unsafe_filesystem_access",
    "dangerous_command": "unsafe_filesystem_access",
}


@dataclass(frozen=True)
class HardBlockerRule:
    """One documented hard-blocker rule."""

    id: str
    source: BlockerSource
    disposition: BlockerDisposition
    summary: str
    detection: str


HARD_BLOCKER_RULES: tuple[HardBlockerRule, ...] = (
    HardBlockerRule(
        id="secret_file_change",
        source="validation",
        disposition="absolute",
        summary="Secret-like file changes detected in the task diff.",
        detection=(
            "Set when secret scanning flags secret-like paths, "
            "or ValidationStatus.secret_scan_clean is False."
        ),
    ),
    HardBlockerRule(
        id="secret_access_attempt",
        source="validation",
        disposition="absolute",
        summary="Worker or reviewer attempted to read or write secret-like files.",
        detection=(
            "Set when monitor stop_reason is secret_access, "
            "or hard_blockers includes secret_access_attempt."
        ),
    ),
    HardBlockerRule(
        id="unsafe_filesystem_access",
        source="validation",
        disposition="absolute",
        summary="Unsafe filesystem or command activity outside policy.",
        detection=(
            "Set when monitor stop_reason is unrelated_directory or dangerous_command, "
            "ValidationStatus.monitor_clean is False, "
            "or hard_blockers includes unsafe_filesystem_access."
        ),
    ),
    HardBlockerRule(
        id="unreviewed_changed_files",
        source="validation",
        disposition="repairable",
        summary="Changed files exist that were not reviewed in the current round.",
        detection="Set when hard_blockers includes unreviewed_changed_files.",
    ),
    HardBlockerRule(
        id="unaccounted_staged_files",
        source="validation",
        disposition="repairable",
        summary="Staged files do not match the manager-approved intended file list.",
        detection="Set when hard_blockers includes unaccounted_staged_files.",
    ),
    HardBlockerRule(
        id="test_failure",
        source="validation",
        disposition="repairable",
        summary="Current-task validation tests failed.",
        detection=(
            "Set when ValidationStatus.tests_passed is False "
            "or hard_blockers includes test_failure."
        ),
    ),
    HardBlockerRule(
        id="severe_correctness_finding",
        source="finding",
        disposition="finding",
        summary="Severe correctness defect that must not ship.",
        detection=(
            "Set for critical-severity findings or correctness findings "
            "with critical severity."
        ),
    ),
    HardBlockerRule(
        id="security_regression_finding",
        source="finding",
        disposition="finding",
        summary="Security regression at critical or high severity.",
        detection=(
            "Set for security findings with critical or high severity."
        ),
    ),
    HardBlockerRule(
        id="dirty_unrelated_work",
        source="validation",
        disposition="repairable",
        summary="Dirty unrelated work remains in the workspace.",
        detection="Set when hard_blockers includes dirty_unrelated_work.",
    ),
    HardBlockerRule(
        id="detached_head",
        source="validation",
        disposition="absolute",
        summary="Repository is on a detached HEAD without explicit allowance.",
        detection=(
            "Set when hard_blockers includes detached_head and "
            "ValidationStatus.allow_detached_head is False."
        ),
    ),
    HardBlockerRule(
        id="missing_pr_credentials",
        source="validation",
        disposition="absolute",
        summary="PR creation is required but credentials are unavailable.",
        detection=(
            "Set when pr_creation_required is True and "
            "pr_credentials_available is False, "
            "or hard_blockers includes missing_pr_credentials."
        ),
    ),
    HardBlockerRule(
        id="scope_risk",
        source="validation",
        disposition="repairable",
        summary="Task scope risk such as unexplained infra or lockfile churn.",
        detection="Set when hard_blockers includes scope_risk.",
    ),
    HardBlockerRule(
        id="staging_risk",
        source="validation",
        disposition="repairable",
        summary="Staging plan risk before manager-owned staging.",
        detection="Set when hard_blockers includes staging_risk.",
    ),
)

HARD_BLOCKER_RULE_BY_ID: dict[str, HardBlockerRule] = {
    rule.id: rule for rule in HARD_BLOCKER_RULES
}


@dataclass(frozen=True)
class ValidationStatus:
    """Deterministic validation signals consumed by manager arbitration."""

    tests_passed: bool = True
    hard_blockers: tuple[str, ...] = ()
    secret_scan_clean: bool = True
    monitor_clean: bool = True
    monitor_stop_reason: str | None = None
    allow_detached_head: bool = False
    pr_creation_required: bool = False
    pr_credentials_available: bool = True


def is_severe_correctness_finding(finding: HocaReviewFinding) -> bool:
    return finding.category == "correctness" and finding.severity == "critical"


def is_security_regression_finding(finding: HocaReviewFinding) -> bool:
    return (
        finding.category == "security"
        and finding.severity in SECURITY_CRITICAL_SEVERITIES
    )


def is_finding_hard_blocker(finding: HocaReviewFinding) -> bool:
    if finding.severity == "critical":
        return True
    if is_security_regression_finding(finding):
        return True
    if is_severe_correctness_finding(finding):
        return True
    return False


def finding_hard_blocker_ids(finding: HocaReviewFinding) -> list[str]:
    if not is_finding_hard_blocker(finding):
        return []
    ids: list[str] = []
    if is_security_regression_finding(finding):
        ids.append("security_regression_finding")
    if is_severe_correctness_finding(finding) or (
        finding.severity == "critical" and finding.category != "security"
    ):
        ids.append("severe_correctness_finding")
    return ids


def validation_blocker_from_monitor_stop_reason(stop_reason: str | None) -> str | None:
    if not stop_reason:
        return None
    return MONITOR_STOP_REASON_TO_BLOCKER.get(stop_reason)


def collect_validation_hard_blockers(validation: ValidationStatus) -> list[str]:
    blockers = list(validation.hard_blockers)

    monitor_blocker = validation_blocker_from_monitor_stop_reason(
        validation.monitor_stop_reason
    )
    if monitor_blocker and monitor_blocker not in blockers:
        blockers.append(monitor_blocker)

    if not validation.tests_passed and "test_failure" not in blockers:
        blockers.append("test_failure")
    if not validation.secret_scan_clean and "secret_file_change" not in blockers:
        blockers.append("secret_file_change")
    if not validation.monitor_clean and "unsafe_filesystem_access" not in blockers:
        blockers.append("unsafe_filesystem_access")
    if (
        validation.pr_creation_required
        and not validation.pr_credentials_available
        and "missing_pr_credentials" not in blockers
    ):
        blockers.append("missing_pr_credentials")
    if validation.allow_detached_head and "detached_head" in blockers:
        blockers = [blocker for blocker in blockers if blocker != "detached_head"]

    return blockers


def has_absolute_validation_blocker(validation: ValidationStatus) -> bool:
    return any(
        blocker in ABSOLUTE_VALIDATION_BLOCKERS
        for blocker in collect_validation_hard_blockers(validation)
    )


def has_repairable_validation_blocker(validation: ValidationStatus) -> bool:
    return any(
        blocker in REPAIRABLE_VALIDATION_BLOCKERS
        for blocker in collect_validation_hard_blockers(validation)
    )


def documented_hard_blocker_ids() -> list[str]:
    return [rule.id for rule in HARD_BLOCKER_RULES]
