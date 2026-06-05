"""Deterministic downgrade rules for HOCA manager arbitration.

Downgraded findings are recorded as PR tech debt instead of forcing another
repair round. Security findings are never downgraded by default. Correctness
findings above low severity always require repair or explicit rejection.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoca.contracts import FindingCategory, FindingSeverity, HocaReviewFinding

DOWNGRADEABLE_SEVERITIES: frozenset[str] = frozenset(("low", "nit"))
NON_DOWNGRADEABLE_CATEGORIES: frozenset[str] = frozenset(("security",))
CORRECTNESS_NON_DOWNGRADEABLE_SEVERITIES: frozenset[str] = frozenset(("critical", "high", "medium"))


@dataclass(frozen=True)
class DowngradeRule:
    """One documented downgrade policy."""

    id: str
    summary: str
    detection: str


DOWNGRADE_RULES: tuple[DowngradeRule, ...] = (
    DowngradeRule(
        id="low_maintainability_tech_debt",
        summary="Low-severity maintainability findings may become PR tech debt.",
        detection=("Applies when severity is low and category is maintainability."),
    ),
    DowngradeRule(
        id="nit_style_tech_debt",
        summary="Nit-severity style findings may become PR tech debt.",
        detection="Applies when severity is nit and category is style.",
    ),
    DowngradeRule(
        id="low_nit_general_tech_debt",
        summary=(
            "Other low or nit findings may become PR tech debt when they are not "
            "security and not medium-or-higher correctness."
        ),
        detection=(
            "Applies when severity is low or nit, category is not security, and "
            "correctness findings are only low severity."
        ),
    ),
    DowngradeRule(
        id="security_never_downgraded",
        summary="Security findings are never downgraded by default.",
        detection="All security categories return can_downgrade_finding=False.",
    ),
    DowngradeRule(
        id="correctness_above_low_never_downgraded",
        summary="Correctness findings above low severity must be repaired or rejected.",
        detection=(
            "Applies when category is correctness and severity is critical, high, or medium."
        ),
    ),
    DowngradeRule(
        id="manager_reasoning_required",
        summary="Every downgrade is recorded in manager decision reasoning.",
        detection=(
            "arbitration.arbitrate appends downgrade_reasoning() for each downgraded finding id."
        ),
    ),
)

DOWNGRADE_RULE_BY_ID: dict[str, DowngradeRule] = {rule.id: rule for rule in DOWNGRADE_RULES}


def documented_downgrade_rule_ids() -> list[str]:
    return [rule.id for rule in DOWNGRADE_RULES]


def can_downgrade_finding(finding: HocaReviewFinding) -> bool:
    """Return True when a finding may be deferred to PR tech debt."""
    if finding.category in NON_DOWNGRADEABLE_CATEGORIES:
        return False
    if (
        finding.category == "correctness"
        and finding.severity in CORRECTNESS_NON_DOWNGRADEABLE_SEVERITIES
    ):
        return False
    return finding.severity in DOWNGRADEABLE_SEVERITIES


def downgrade_reasoning(finding: HocaReviewFinding) -> str:
    """Produce deterministic manager reasoning for a downgraded finding."""
    return (
        f"{finding.id} downgraded to PR tech debt ({finding.severity} "
        f"{finding.category}): inconsequential for this PR; preserved in PR notes."
    )


def format_downgraded_finding_note(finding: HocaReviewFinding) -> str:
    location = f" ({finding.file})" if finding.file else ""
    fix = finding.required_fix or finding.summary
    return f"{finding.id}{location}: {fix}"


def merge_downgraded_findings_into_pr_notes(
    pr_notes: dict[str, list[str]],
    findings_by_id: dict[str, HocaReviewFinding],
    downgraded_finding_ids: list[str],
) -> dict[str, list[str]]:
    """Preserve downgraded findings in PR notes without discarding existing content."""
    if not downgraded_finding_ids:
        return pr_notes

    merged = {key: list(value) for key, value in pr_notes.items()}
    followups = list(merged.get("known_followups", []))
    seen = set(followups)

    for finding_id in downgraded_finding_ids:
        finding = findings_by_id.get(finding_id)
        if finding is None:
            note = f"{finding_id}: reviewer finding deferred to PR follow-up (manager downgrade)"
        else:
            note = format_downgraded_finding_note(finding)
        if note not in seen:
            followups.append(note)
            seen.add(note)

    merged["known_followups"] = followups
    return merged


def matching_downgrade_rule_ids(
    severity: FindingSeverity,
    category: FindingCategory,
) -> list[str]:
    """Return documented rule ids that apply to a severity/category pair."""
    matched: list[str] = []
    if category == "security":
        return ["security_never_downgraded"]
    if category == "correctness" and severity in CORRECTNESS_NON_DOWNGRADEABLE_SEVERITIES:
        return ["correctness_above_low_never_downgraded"]
    if severity == "low" and category == "maintainability":
        matched.append("low_maintainability_tech_debt")
    if severity == "nit" and category == "style":
        matched.append("nit_style_tech_debt")
    if severity in DOWNGRADEABLE_SEVERITIES and category != "security":
        if "low_nit_general_tech_debt" not in matched:
            matched.append("low_nit_general_tech_debt")
    return matched
