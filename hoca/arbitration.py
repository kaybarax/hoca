from __future__ import annotations

from typing import Literal

from hoca.contracts import (
    FindingSeverity,
    HocaManagerDecision,
    HocaReviewFinding,
    HocaReviewReport,
    ManagerDecision,
)
from hoca.downgrade_rules import can_downgrade_finding, downgrade_reasoning
from hoca.hard_blockers import (
    ValidationStatus,
    collect_validation_hard_blockers,
    has_absolute_validation_blocker,
    has_repairable_validation_blocker,
    is_finding_hard_blocker,
)

FindingDisposition = Literal["repair", "downgrade", "reject"]

SEVERITY_RANK: dict[FindingSeverity, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "nit": 4,
}


def sort_findings_by_severity(
    findings: list[HocaReviewFinding],
) -> list[HocaReviewFinding]:
    return sorted(findings, key=lambda finding: (SEVERITY_RANK[finding.severity], finding.id))


def finding_requires_repair(
    finding: HocaReviewFinding,
    *,
    explicitly_impossible: frozenset[str] = frozenset(),
) -> bool:
    if finding.id in explicitly_impossible:
        return False
    if is_finding_hard_blocker(finding):
        return True
    if finding.severity == "high" and finding.category in ("correctness", "security"):
        return True
    if finding.severity == "medium" and finding.category == "security":
        return True
    if finding.severity == "medium" and finding.category == "correctness":
        return True
    if finding.severity == "medium":
        return True
    return False


def classify_finding(
    finding: HocaReviewFinding,
    *,
    explicitly_impossible: frozenset[str] = frozenset(),
) -> FindingDisposition:
    if finding.id in explicitly_impossible:
        return "reject"
    if can_downgrade_finding(finding):
        return "downgrade"
    if finding_requires_repair(finding, explicitly_impossible=explicitly_impossible):
        return "repair"
    return "reject"


def generate_repair_brief(
    *,
    accepted_findings: list[str],
    rejected_findings: list[str],
    downgraded_findings: list[str],
    findings_by_id: dict[str, HocaReviewFinding],
) -> str:
    if not accepted_findings:
        raise ValueError("accepted_findings must not be empty for a repair brief")

    lines = [
        "Fix only the accepted reviewer findings in this round.",
        f"Accepted findings: {', '.join(accepted_findings)}.",
    ]
    for finding_id in accepted_findings:
        finding = findings_by_id[finding_id]
        fix = finding.required_fix or finding.summary
        location = f" ({finding.file})" if finding.file else ""
        lines.append(f"- {finding_id}{location}: {fix}")

    if rejected_findings:
        lines.append(f"Do not address rejected findings: {', '.join(rejected_findings)}.")
    if downgraded_findings:
        lines.append(
            f"Leave downgraded findings for PR follow-up: {', '.join(downgraded_findings)}."
        )
    lines.append("Keep changes minimal and do not restart unrelated work.")
    return "\n".join(lines) + "\n"


def _blocked_decision(
    *,
    review: HocaReviewReport,
    accepted_findings: list[str],
    rejected_findings: list[str],
    downgraded_to_pr_notes: list[str],
    reasoning: list[str],
    human_attention_required: bool = True,
) -> HocaManagerDecision:
    return HocaManagerDecision(
        run_id=review.run_id,
        round=review.round,
        decision="blocked",
        accepted_findings=accepted_findings,
        rejected_findings=rejected_findings,
        downgraded_to_pr_notes=downgraded_to_pr_notes,
        reasoning=reasoning,
        next_worker_brief=None,
        human_attention_required=human_attention_required,
    )


def _decision_from_material_findings(
    *,
    review: HocaReviewReport,
    accepted_findings: list[str],
    rejected_findings: list[str],
    downgraded_to_pr_notes: list[str],
    reasoning: list[str],
    findings_by_id: dict[str, HocaReviewFinding],
    max_total_rounds: int,
    final_round: bool,
) -> HocaManagerDecision:
    material_accepted = [
        finding_id
        for finding_id in accepted_findings
        if finding_id in findings_by_id and not can_downgrade_finding(findings_by_id[finding_id])
    ]
    hard_blockers = [
        finding_id
        for finding_id in material_accepted
        if is_finding_hard_blocker(findings_by_id[finding_id])
    ]
    high_material = [
        finding_id
        for finding_id in material_accepted
        if findings_by_id[finding_id].severity == "high"
    ]
    medium_residual = [
        finding_id
        for finding_id in material_accepted
        if findings_by_id[finding_id].severity == "medium"
    ]

    if final_round and hard_blockers:
        reasoning.append("Round cap reached with hard-blocker findings; run is blocked.")
        return _blocked_decision(
            review=review,
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
        )

    if final_round and high_material:
        reasoning.append(
            "Round cap reached with unresolved high-severity findings; run is blocked."
        )
        return _blocked_decision(
            review=review,
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
        )

    if final_round and medium_residual:
        reasoning.append(
            "Round cap reached with medium residual findings and no hard blockers; "
            "opening a draft PR with clearly marked follow-up work."
        )
        return HocaManagerDecision(
            run_id=review.run_id,
            round=review.round,
            decision="draft_pr_with_blockers",
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
            next_worker_brief=None,
            human_attention_required=True,
        )

    if material_accepted and review.round < max_total_rounds:
        reasoning.append("Material findings require another focused repair round.")
        return HocaManagerDecision(
            run_id=review.run_id,
            round=review.round,
            decision="repair_required",
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
            next_worker_brief=generate_repair_brief(
                accepted_findings=material_accepted,
                rejected_findings=rejected_findings,
                downgraded_findings=downgraded_to_pr_notes,
                findings_by_id=findings_by_id,
            ),
            human_attention_required=False,
        )

    reasoning.append("No material findings remain; proceed to PR.")
    return HocaManagerDecision(
        run_id=review.run_id,
        round=review.round,
        decision="proceed_to_pr",
        accepted_findings=accepted_findings,
        rejected_findings=rejected_findings,
        downgraded_to_pr_notes=downgraded_to_pr_notes,
        reasoning=reasoning,
        next_worker_brief=None,
        human_attention_required=bool(downgraded_to_pr_notes),
    )


def _validation_blockers_for_arbitration(
    *,
    review: HocaReviewReport,
    validation: ValidationStatus,
    reasoning: list[str],
) -> list[str]:
    blockers = collect_validation_hard_blockers(validation)
    if (
        review.verdict == "LGTM"
        and validation.tests_passed
        and validation.secret_scan_clean
        and validation.monitor_stop_reason == "dangerous_command"
        and set(blockers) == {"unsafe_filesystem_access"}
    ):
        reasoning.append(
            "Reviewer approved after successful validation; treating monitor "
            "dangerous_command stop as a non-blocking environment note."
        )
        return []
    return blockers


def arbitrate(
    *,
    review: HocaReviewReport,
    validation: ValidationStatus,
    max_total_rounds: int = 3,
    explicitly_impossible: frozenset[str] = frozenset(),
) -> HocaManagerDecision:
    """Produce a deterministic manager decision from review and validation signals."""
    if max_total_rounds < 1:
        raise ValueError("max_total_rounds must be greater than or equal to 1")

    final_round = review.round >= max_total_rounds
    sorted_findings = sort_findings_by_severity(review.findings)
    findings_by_id = {finding.id: finding for finding in sorted_findings}

    accepted_findings: list[str] = []
    rejected_findings: list[str] = []
    downgraded_to_pr_notes: list[str] = []
    reasoning: list[str] = []

    if review.verdict == "blocked":
        reasoning.append("Reviewer verdict is blocked.")
        for finding in sorted_findings:
            if is_finding_hard_blocker(finding):
                accepted_findings.append(finding.id)
                reasoning.append(f"{finding.id} is a hard-blocker finding.")
            else:
                rejected_findings.append(finding.id)
        return _blocked_decision(
            review=review,
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
        )

    validation_blockers = _validation_blockers_for_arbitration(
        review=review,
        validation=validation,
        reasoning=reasoning,
    )
    if any(
        blocker
        in {
            "secret_file_change",
            "secret_access_attempt",
            "unsafe_filesystem_access",
            "missing_pr_credentials",
            "detached_head",
        }
        for blocker in validation_blockers
    ):
        reasoning.append(
            "Absolute validation hard blocker detected: "
            + ", ".join(sorted(set(validation_blockers)))
        )
        return _blocked_decision(
            review=review,
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
        )

    for finding in sorted_findings:
        disposition = classify_finding(finding, explicitly_impossible=explicitly_impossible)
        if disposition == "repair":
            accepted_findings.append(finding.id)
            reasoning.append(
                f"{finding.id} accepted for repair ({finding.severity} {finding.category})."
            )
        elif disposition == "downgrade":
            downgraded_to_pr_notes.append(finding.id)
            reasoning.append(downgrade_reasoning(finding))
        else:
            if finding.id in explicitly_impossible:
                rejected_findings.append(finding.id)
                reasoning.append(f"{finding.id} rejected as explicitly impossible to fix.")
            else:
                rejected_findings.append(finding.id)
                reasoning.append(
                    f"{finding.id} rejected as inconsequential ({finding.severity} {finding.category})."
                )

    if validation_blockers:
        if final_round or not has_repairable_validation_blocker(validation):
            reasoning.append(
                "Validation hard blocker remains at round cap: "
                + ", ".join(sorted(set(validation_blockers)))
            )
            return _blocked_decision(
                review=review,
                accepted_findings=accepted_findings,
                rejected_findings=rejected_findings,
                downgraded_to_pr_notes=downgraded_to_pr_notes,
                reasoning=reasoning,
            )
        reasoning.append(
            "Validation issues require repair before PR: "
            + ", ".join(sorted(set(validation_blockers)))
        )
        repair_ids = accepted_findings or ["validation"]
        return HocaManagerDecision(
            run_id=review.run_id,
            round=review.round,
            decision="repair_required",
            accepted_findings=accepted_findings,
            rejected_findings=rejected_findings,
            downgraded_to_pr_notes=downgraded_to_pr_notes,
            reasoning=reasoning,
            next_worker_brief=(
                generate_repair_brief(
                    accepted_findings=repair_ids,
                    rejected_findings=rejected_findings,
                    downgraded_findings=downgraded_to_pr_notes,
                    findings_by_id=findings_by_id,
                )
                if accepted_findings
                else (
                    "Resolve validation failures before PR. "
                    f"Blockers: {', '.join(sorted(set(validation_blockers)))}.\n"
                )
            ),
            human_attention_required=False,
        )

    return _decision_from_material_findings(
        review=review,
        accepted_findings=accepted_findings,
        rejected_findings=rejected_findings,
        downgraded_to_pr_notes=downgraded_to_pr_notes,
        reasoning=reasoning,
        findings_by_id=findings_by_id,
        max_total_rounds=max_total_rounds,
        final_round=final_round,
    )


def decision_for_review(
    review: HocaReviewReport,
    validation: ValidationStatus,
    *,
    max_total_rounds: int = 3,
    explicitly_impossible: frozenset[str] = frozenset(),
) -> ManagerDecision:
    """Return only the decision enum for lightweight callers."""
    return arbitrate(
        review=review,
        validation=validation,
        max_total_rounds=max_total_rounds,
        explicitly_impossible=explicitly_impossible,
    ).decision
