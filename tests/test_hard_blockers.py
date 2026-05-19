from __future__ import annotations

import pytest

from hoca.arbitration import arbitrate
from hoca.contracts import HocaReviewFinding, HocaReviewReport
from hoca.hard_blockers import (
    ABSOLUTE_VALIDATION_BLOCKERS,
    HARD_BLOCKER_RULES,
    REPAIRABLE_VALIDATION_BLOCKERS,
    ValidationStatus,
    collect_validation_hard_blockers,
    documented_hard_blocker_ids,
    finding_hard_blocker_ids,
    has_absolute_validation_blocker,
    is_finding_hard_blocker,
    is_security_regression_finding,
    is_severe_correctness_finding,
    validation_blocker_from_monitor_stop_reason,
)


def _finding(
    finding_id: str,
    *,
    severity: str = "medium",
    category: str = "correctness",
) -> HocaReviewFinding:
    return HocaReviewFinding(
        id=finding_id,
        severity=severity,
        category=category,
        file="src/main.py",
        summary="Issue",
        required_fix="Fix it",
    )


def _review(
    *,
    round_number: int = 3,
    verdict: str = "LGTM",
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


class TestHardBlockerCatalog:
    def test_catalog_documents_all_task_blockers(self) -> None:
        required = {
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
        }
        documented = set(documented_hard_blocker_ids())
        assert required <= documented

    def test_every_rule_has_documentation_fields(self) -> None:
        for rule in HARD_BLOCKER_RULES:
            assert rule.id
            assert rule.summary
            assert rule.detection
            assert rule.source in ("validation", "finding")
            assert rule.disposition in ("absolute", "repairable", "finding")

    def test_validation_blocker_sets_are_disjoint(self) -> None:
        assert ABSOLUTE_VALIDATION_BLOCKERS.isdisjoint(REPAIRABLE_VALIDATION_BLOCKERS)


class TestValidationHardBlockers:
    @pytest.mark.parametrize(
        ("validation", "expected_blocker"),
        [
            (ValidationStatus(secret_scan_clean=False), "secret_file_change"),
            (
                ValidationStatus(monitor_stop_reason="secret_access"),
                "secret_access_attempt",
            ),
            (
                ValidationStatus(monitor_stop_reason="unrelated_directory"),
                "unsafe_filesystem_access",
            ),
            (
                ValidationStatus(monitor_stop_reason="dangerous_command"),
                "unsafe_filesystem_access",
            ),
            (ValidationStatus(monitor_clean=False), "unsafe_filesystem_access"),
            (
                ValidationStatus(hard_blockers=("unreviewed_changed_files",)),
                "unreviewed_changed_files",
            ),
            (
                ValidationStatus(hard_blockers=("unaccounted_staged_files",)),
                "unaccounted_staged_files",
            ),
            (ValidationStatus(tests_passed=False), "test_failure"),
            (
                ValidationStatus(hard_blockers=("dirty_unrelated_work",)),
                "dirty_unrelated_work",
            ),
            (ValidationStatus(hard_blockers=("detached_head",)), "detached_head"),
            (
                ValidationStatus(
                    pr_creation_required=True,
                    pr_credentials_available=False,
                ),
                "missing_pr_credentials",
            ),
        ],
    )
    def test_collect_validation_hard_blockers(
        self, validation: ValidationStatus, expected_blocker: str
    ) -> None:
        blockers = collect_validation_hard_blockers(validation)
        assert expected_blocker in blockers

    def test_detached_head_allowed_when_configured(self) -> None:
        validation = ValidationStatus(
            hard_blockers=("detached_head",),
            allow_detached_head=True,
        )
        assert "detached_head" not in collect_validation_hard_blockers(validation)

    @pytest.mark.parametrize(
        ("stop_reason", "blocker_id"),
        [
            ("secret_access", "secret_access_attempt"),
            ("unrelated_directory", "unsafe_filesystem_access"),
            ("dangerous_command", "unsafe_filesystem_access"),
            ("completed", None),
        ],
    )
    def test_monitor_stop_reason_mapping(
        self, stop_reason: str, blocker_id: str | None
    ) -> None:
        assert validation_blocker_from_monitor_stop_reason(stop_reason) == blocker_id


class TestFindingHardBlockers:
    @pytest.mark.parametrize(
        ("severity", "category", "expected"),
        [
            ("critical", "correctness", True),
            ("critical", "maintainability", True),
            ("high", "security", True),
            ("high", "correctness", False),
            ("medium", "security", False),
        ],
    )
    def test_is_finding_hard_blocker(
        self, severity: str, category: str, expected: bool
    ) -> None:
        finding = _finding("F1", severity=severity, category=category)
        assert is_finding_hard_blocker(finding) is expected

    def test_severe_correctness_finding_ids(self) -> None:
        finding = _finding("F1", severity="critical", category="correctness")
        assert is_severe_correctness_finding(finding) is True
        assert "severe_correctness_finding" in finding_hard_blocker_ids(finding)

    def test_security_regression_finding_ids(self) -> None:
        finding = _finding("F1", severity="high", category="security")
        assert is_security_regression_finding(finding) is True
        assert "security_regression_finding" in finding_hard_blocker_ids(finding)


class TestHardBlockerArbitrationIntegration:
    def _validation_for_blocker(self, blocker_id: str) -> ValidationStatus:
        if blocker_id == "secret_file_change":
            return ValidationStatus(secret_scan_clean=False)
        if blocker_id == "unsafe_filesystem_access":
            return ValidationStatus(monitor_clean=False)
        if blocker_id == "missing_pr_credentials":
            return ValidationStatus(
                pr_creation_required=True,
                pr_credentials_available=False,
            )
        if blocker_id == "secret_access_attempt":
            return ValidationStatus(monitor_stop_reason="secret_access")
        return ValidationStatus(hard_blockers=(blocker_id,))

    @pytest.mark.parametrize(
        "blocker_id",
        sorted(ABSOLUTE_VALIDATION_BLOCKERS),
    )
    def test_absolute_validation_blocker_blocks_immediately(
        self, blocker_id: str
    ) -> None:
        validation = self._validation_for_blocker(blocker_id)
        decision = arbitrate(
            review=_review(round_number=1, verdict="LGTM"),
            validation=validation,
        )
        assert decision.decision == "blocked"
        assert has_absolute_validation_blocker(validation)

    @pytest.mark.parametrize(
        "blocker_id",
        sorted(REPAIRABLE_VALIDATION_BLOCKERS),
    )
    def test_repairable_validation_blocker_blocks_at_round_cap(
        self, blocker_id: str
    ) -> None:
        if blocker_id == "test_failure":
            validation = ValidationStatus(tests_passed=False)
        else:
            validation = ValidationStatus(hard_blockers=(blocker_id,))
        decision = arbitrate(
            review=_review(round_number=3, verdict="LGTM"),
            validation=validation,
            max_total_rounds=3,
        )
        assert decision.decision == "blocked"

    def test_critical_finding_blocks_at_round_cap(self) -> None:
        decision = arbitrate(
            review=_review(
                round_number=3,
                findings=[_finding("F1", severity="critical", category="security")],
            ),
            validation=ValidationStatus(),
            max_total_rounds=3,
        )
        assert decision.decision == "blocked"
