from __future__ import annotations

import pytest

from hoca.arbitration import (
    ValidationStatus,
    arbitrate,
    can_downgrade_finding,
    classify_finding,
    collect_validation_hard_blockers,
    decision_for_review,
    finding_requires_repair,
    generate_repair_brief,
    is_finding_hard_blocker,
    sort_findings_by_severity,
)
from hoca.contracts import HocaReviewFinding, HocaReviewReport


def _finding(
    finding_id: str,
    *,
    severity: str = "medium",
    category: str = "correctness",
    required_fix: str | None = "Fix it",
    summary: str = "Issue",
) -> HocaReviewFinding:
    return HocaReviewFinding(
        id=finding_id,
        severity=severity,
        category=category,
        file="src/main.py",
        summary=summary,
        required_fix=required_fix,
    )


def _review(
    *,
    round_number: int = 1,
    verdict: str = "fix_required",
    findings: list[HocaReviewFinding] | None = None,
) -> HocaReviewReport:
    return HocaReviewReport(
        run_id="run-123",
        round=round_number,
        role="reviewer",
        verdict=verdict,
        findings=findings or [],
        pr_notes={"summary": [], "known_followups": []},
    )


class TestSeveritySorting:
    def test_sort_findings_by_severity(self) -> None:
        findings = [
            _finding("F-low", severity="low", category="style", required_fix=None),
            _finding("F-critical", severity="critical", category="security"),
            _finding("F-medium", severity="medium", category="test"),
            _finding("F-high", severity="high", category="correctness"),
        ]

        sorted_ids = [finding.id for finding in sort_findings_by_severity(findings)]

        assert sorted_ids == ["F-critical", "F-high", "F-medium", "F-low"]


class TestHardBlockerDetection:
    @pytest.mark.parametrize(
        ("severity", "category", "expected"),
        [
            ("critical", "maintainability", True),
            ("high", "security", True),
            ("high", "style", False),
            ("medium", "security", False),
        ],
    )
    def test_is_finding_hard_blocker(
        self, severity: str, category: str, expected: bool
    ) -> None:
        finding = _finding("F1", severity=severity, category=category)
        assert is_finding_hard_blocker(finding) is expected

    def test_collect_validation_hard_blockers_from_flags(self) -> None:
        validation = ValidationStatus(
            tests_passed=False,
            secret_scan_clean=False,
            monitor_clean=False,
        )

        blockers = collect_validation_hard_blockers(validation)

        assert "test_failure" in blockers
        assert "secret_file_change" in blockers
        assert "unsafe_filesystem_access" in blockers


class TestFindingClassification:
    def test_critical_always_requires_repair(self) -> None:
        finding = _finding("F1", severity="critical", category="maintainability")
        assert finding_requires_repair(finding) is True
        assert classify_finding(finding) == "repair"

    def test_high_correctness_requires_repair(self) -> None:
        finding = _finding("F1", severity="high", category="correctness")
        assert finding_requires_repair(finding) is True

    def test_high_correctness_can_be_marked_impossible(self) -> None:
        finding = _finding("F1", severity="high", category="correctness")
        assert (
            finding_requires_repair(
                finding, explicitly_impossible=frozenset({"F1"})
            )
            is False
        )
        assert (
            classify_finding(finding, explicitly_impossible=frozenset({"F1"}))
            == "reject"
        )

    def test_low_style_can_downgrade(self) -> None:
        finding = _finding(
            "F1", severity="low", category="style", required_fix=None
        )
        assert can_downgrade_finding(finding) is True
        assert classify_finding(finding) == "downgrade"

    def test_security_low_cannot_downgrade(self) -> None:
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


class TestRepairBrief:
    def test_generate_repair_brief_is_focused(self) -> None:
        findings = {
            "F1": _finding("F1", severity="high", category="correctness"),
            "F2": _finding("F2", severity="low", category="style", required_fix=None),
        }

        brief = generate_repair_brief(
            accepted_findings=["F1"],
            rejected_findings=["R1"],
            downgraded_findings=["F2"],
            findings_by_id=findings,
        )

        assert "Fix only the accepted reviewer findings" in brief
        assert "F1" in brief
        assert "Do not address rejected findings: R1." in brief
        assert "Leave downgraded findings for PR follow-up: F2." in brief


class TestArbitrate:
    def test_lgtm_after_round_1_proceeds_to_pr(self) -> None:
        decision = arbitrate(
            review=_review(round_number=1, verdict="LGTM"),
            validation=ValidationStatus(),
        )

        assert decision.decision == "proceed_to_pr"
        assert decision.next_worker_brief is None

    def test_fix_required_round_1_goes_to_repair(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=1,
                findings=[_finding("F1", severity="high", category="correctness")],
            ),
            validation=ValidationStatus(),
        )

        assert decision.decision == "repair_required"
        assert decision.accepted_findings == ["F1"]
        assert decision.next_worker_brief is not None
        assert "F1" in decision.next_worker_brief

    def test_fix_required_round_2_goes_to_repair(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=2,
                findings=[_finding("F1", severity="medium", category="test")],
            ),
            validation=ValidationStatus(),
            max_total_rounds=3,
        )

        assert decision.decision == "repair_required"

    def test_low_priority_after_round_3_proceeds_with_pr_notes(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=3,
                verdict="LGTM",
                findings=[
                    _finding(
                        "F1",
                        severity="nit",
                        category="style",
                        required_fix=None,
                    )
                ],
            ),
            validation=ValidationStatus(),
            max_total_rounds=3,
        )

        assert decision.decision == "proceed_to_pr"
        assert decision.downgraded_to_pr_notes == ["F1"]

    def test_medium_residual_after_round_3_produces_draft_pr(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=3,
                findings=[_finding("F1", severity="medium", category="test")],
            ),
            validation=ValidationStatus(),
            max_total_rounds=3,
        )

        assert decision.decision == "draft_pr_with_blockers"
        assert decision.human_attention_required is True
        assert "medium residual" in " ".join(decision.reasoning).lower()

    def test_critical_after_round_3_blocks(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=3,
                findings=[_finding("F1", severity="critical", category="security")],
            ),
            validation=ValidationStatus(),
            max_total_rounds=3,
        )

        assert decision.decision == "blocked"
        assert decision.next_worker_brief is None

    def test_test_failure_after_round_3_blocks(self) -> None:
        decision = arbitrate(
            review=_review(round_number=3, verdict="LGTM"),
            validation=ValidationStatus(tests_passed=False),
            max_total_rounds=3,
        )

        assert decision.decision == "blocked"

    def test_test_failure_before_round_cap_requires_repair(self) -> None:
        decision = arbitrate(
            review=_review(round_number=1, verdict="LGTM"),
            validation=ValidationStatus(tests_passed=False),
            max_total_rounds=3,
        )

        assert decision.decision == "repair_required"
        assert "validation" in decision.next_worker_brief.lower()

    def test_secret_blocker_blocks_immediately(self) -> None:
        decision = arbitrate(
            review=_review(round_number=1, verdict="LGTM"),
            validation=ValidationStatus(
                hard_blockers=("secret_file_change",),
            ),
        )

        assert decision.decision == "blocked"

    def test_blocked_verdict_blocks(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=1,
                verdict="blocked",
                findings=[_finding("F1", severity="critical", category="security")],
            ),
            validation=ValidationStatus(),
        )

        assert decision.decision == "blocked"

    def test_high_security_impossible_is_rejected_not_repair(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=1,
                findings=[_finding("F1", severity="high", category="security")],
            ),
            validation=ValidationStatus(),
            explicitly_impossible=frozenset({"F1"}),
        )

        assert decision.decision == "proceed_to_pr"
        assert decision.rejected_findings == ["F1"]

    def test_decision_for_review_returns_enum(self) -> None:
        assert (
            decision_for_review(
                _review(round_number=1, verdict="LGTM"),
                ValidationStatus(),
            )
            == "proceed_to_pr"
        )

    def test_arbitrate_rejects_invalid_max_rounds(self) -> None:
        with pytest.raises(ValueError, match="max_total_rounds"):
            arbitrate(
                review=_review(round_number=1, verdict="LGTM"),
                validation=ValidationStatus(),
                max_total_rounds=0,
            )
