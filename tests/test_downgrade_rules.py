from __future__ import annotations

import pytest

from hoca.arbitration import arbitrate, classify_finding
from hoca.contracts import HocaReviewFinding, HocaReviewReport
from hoca.downgrade_rules import (
    DOWNGRADE_RULES,
    can_downgrade_finding,
    documented_downgrade_rule_ids,
    downgrade_reasoning,
    format_downgraded_finding_note,
    matching_downgrade_rule_ids,
    merge_downgraded_findings_into_pr_notes,
)
from hoca.hard_blockers import ValidationStatus


def _finding(
    finding_id: str,
    *,
    severity: str = "low",
    category: str = "style",
    required_fix: str | None = None,
    summary: str = "Minor issue",
) -> HocaReviewFinding:
    return HocaReviewFinding(
        id=finding_id,
        severity=severity,
        category=category,
        file="src/module.py",
        summary=summary,
        required_fix=required_fix,
    )


def _review(
    *,
    findings: list[HocaReviewFinding],
    round_number: int = 1,
    verdict: str = "LGTM",
) -> HocaReviewReport:
    return HocaReviewReport(
        run_id="run-123",
        round=round_number,
        role="reviewer",
        verdict=verdict,
        findings=findings,
        pr_notes={"summary": [], "known_followups": []},
    )


class TestDowngradeCatalog:
    def test_catalog_documents_task_rules(self) -> None:
        required = {
            "low_maintainability_tech_debt",
            "nit_style_tech_debt",
            "security_never_downgraded",
            "correctness_above_low_never_downgraded",
            "manager_reasoning_required",
        }
        documented = set(documented_downgrade_rule_ids())
        assert required <= documented

    def test_every_rule_has_documentation_fields(self) -> None:
        for rule in DOWNGRADE_RULES:
            assert rule.id
            assert rule.summary
            assert rule.detection


class TestCanDowngradeFinding:
    def test_low_maintainability_can_downgrade(self) -> None:
        finding = _finding("F1", severity="low", category="maintainability")
        assert can_downgrade_finding(finding) is True
        assert "low_maintainability_tech_debt" in matching_downgrade_rule_ids(
            "low", "maintainability"
        )

    def test_nit_style_can_downgrade(self) -> None:
        finding = _finding("F1", severity="nit", category="style")
        assert can_downgrade_finding(finding) is True
        assert classify_finding(finding) == "downgrade"

    def test_security_never_downgrades(self) -> None:
        with pytest.raises(ValueError, match="Security findings must have severity"):
            HocaReviewFinding.from_dict(
                {
                    "id": "F1",
                    "severity": "low",
                    "category": "security",
                    "file": None,
                    "summary": "Minor security note",
                    "required_fix": None,
                }
            )

    @pytest.mark.parametrize(
        "severity",
        ["critical", "high", "medium"],
    )
    def test_correctness_above_low_cannot_downgrade(self, severity: str) -> None:
        finding = _finding("F1", severity=severity, category="correctness")
        assert can_downgrade_finding(finding) is False
        assert matching_downgrade_rule_ids(severity, "correctness") == [
            "correctness_above_low_never_downgraded"
        ]

    def test_low_correctness_can_downgrade(self) -> None:
        finding = _finding("F1", severity="low", category="correctness")
        assert can_downgrade_finding(finding) is True

    def test_medium_style_cannot_downgrade(self) -> None:
        finding = _finding("F1", severity="medium", category="style")
        assert can_downgrade_finding(finding) is False


class TestManagerReasoning:
    def test_downgrade_reasoning_mentions_pr_preservation(self) -> None:
        finding = _finding("F2", severity="nit", category="style")
        text = downgrade_reasoning(finding)
        assert "F2" in text
        assert "PR tech debt" in text
        assert "PR notes" in text

    def test_arbitrate_records_reasoning_for_downgrades(self) -> None:
        decision = arbitrate(
            review=_review(
                findings=[_finding("F1", severity="nit", category="style")],
            ),
            validation=ValidationStatus(),
        )
        assert decision.downgraded_to_pr_notes == ["F1"]
        assert any("downgraded to PR tech debt" in line for line in decision.reasoning)


class TestPrNotesPreservation:
    def test_merge_downgraded_findings_into_pr_notes(self) -> None:
        finding = _finding(
            "F1",
            severity="low",
            category="maintainability",
            required_fix=None,
            summary="Extract helper",
        )
        merged = merge_downgraded_findings_into_pr_notes(
            {"summary": ["Ready"], "known_followups": ["Existing item"]},
            {"F1": finding},
            ["F1"],
        )

        assert merged["summary"] == ["Ready"]
        assert "Existing item" in merged["known_followups"]
        assert format_downgraded_finding_note(finding) in merged["known_followups"]

    def test_merge_does_not_duplicate_notes(self) -> None:
        finding = _finding("F1", severity="nit", category="style")
        note = format_downgraded_finding_note(finding)
        merged = merge_downgraded_findings_into_pr_notes(
            {"known_followups": [note]},
            {"F1": finding},
            ["F1"],
        )
        assert merged["known_followups"].count(note) == 1

    def test_merge_unknown_finding_id_still_preserved(self) -> None:
        merged = merge_downgraded_findings_into_pr_notes(
            {"known_followups": []},
            {},
            ["F-missing"],
        )
        assert any("F-missing" in item for item in merged["known_followups"])


class TestReviewerOverride:
    def test_inconsequential_high_style_finding_rejected(self) -> None:
        finding = _finding("F1", severity="high", category="style")
        decision = arbitrate(
            review=_review(findings=[finding], verdict="fix_required"),
            validation=ValidationStatus(),
        )
        assert decision.rejected_findings == ["F1"]
        assert decision.accepted_findings == []

    def test_downgraded_and_material_findings_split(self) -> None:
        decision = arbitrate(
            review=_review(
                findings=[
                    _finding("F-repair", severity="high", category="correctness"),
                    _finding("F-debt", severity="nit", category="style"),
                ],
                verdict="fix_required",
            ),
            validation=ValidationStatus(),
        )
        assert decision.accepted_findings == ["F-repair"]
        assert decision.downgraded_to_pr_notes == ["F-debt"]
