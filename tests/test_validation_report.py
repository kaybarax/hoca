from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoca.arbitration import arbitrate
from hoca.contracts import HocaReviewReport, HocaTaskSpec, HocaValidationReport
from hoca.hard_blockers import ValidationStatus
from hoca.run_artifacts import build_validation_status_from_run_dir, record_validation_report
from hoca.run_layout import ensure_run_layout, validation_report_path
from hoca.run_state import summarize_run_for_pr_body, write_json_atomic
from hoca.task_spec import build_enriched_task_spec
from hoca.validation_assessment import (
    assess_validation_risks,
    is_dependency_lockfile,
    is_infrastructure_file,
    path_matches_task_context,
)


def _review(*, round_number: int = 1, verdict: str = "LGTM") -> HocaReviewReport:
    return HocaReviewReport(
        run_id="run-val",
        round=round_number,
        role="reviewer",
        verdict=verdict,
        findings=[],
        pr_notes={"summary": ["OK"], "known_followups": []},
    )


def _task_spec(tmp_path: Path, *, expected_areas: list[str], goal: str) -> HocaTaskSpec:
    base = HocaTaskSpec(
        run_id="run-val",
        repo_root=str(tmp_path),
        base_branch="main",
        task_branch="feat/example",
        issue_id=None,
        raw_request=goal,
        goal=goal,
        non_goals=[],
        expected_areas=[],
        acceptance_criteria=[],
        test_commands=[],
        risk_level="low",
        requires_human_approval=False,
        max_total_rounds=3,
        models={"manager": "m", "worker": "w", "reviewer": "r", "fallback": "f"},
        sandbox={"enabled": False, "network_mode": "offline"},
    )
    return build_enriched_task_spec(
        base_spec=base,
        instruction_summaries=[],
        test_commands=[],
        expected_areas=expected_areas,
    )


class TestValidationAssessmentHelpers:
    def test_path_matches_expected_area_prefix(self) -> None:
        assert path_matches_task_context(
            "apps/api/src/auth.py",
            expected_areas=["apps/api"],
            task_tokens=frozenset(),
            justified_files=set(),
        )

    def test_lockfile_requires_justification_for_staging_risk(self) -> None:
        assert is_dependency_lockfile("package-lock.json")


class TestRecordValidationReport:
    def test_writes_structured_report_with_all_signals(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-val"
        ensure_run_layout(run_dir)
        spec = _task_spec(tmp_path, expected_areas=["src/auth"], goal="Update auth reset")
        write_json_atomic(run_dir / "task-spec.json", spec.to_dict())

        (run_dir / "git-status.txt").write_text(" M src/other/unrelated.py\n", encoding="utf-8")
        (run_dir / "changed-files.txt").write_text(
            "src/other/unrelated.py\npackage-lock.json\n",
            encoding="utf-8",
        )
        (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")
        (run_dir / "tests-summary.md").write_text("# Tests\n\n- **Status**: passed\n", encoding="utf-8")
        (run_dir / "secret-detected.txt").write_text("src/other/unrelated.py\n", encoding="utf-8")
        write_json_atomic(
            run_dir / "monitor-result.json",
            {"stop_reason": "secret_access", "status": "stopped"},
        )

        path = record_validation_report(run_dir, round_number=1)
        report = HocaValidationReport.from_json(path.read_text(encoding="utf-8"))

        assert path == validation_report_path(run_dir, 1)
        assert report.run_id == "run-val"
        assert report.round == 1
        assert report.tests_passed is True
        assert report.git_status == ["M src/other/unrelated.py"]
        assert report.changed_files == ["src/other/unrelated.py", "package-lock.json"]
        assert report.secret_scan_clean is False
        assert report.monitor_clean is False
        assert report.monitor_stop_reason == "secret_access"
        assert report.scope_risk is True
        assert report.staging_risk is True
        assert "secret_file_change" in report.hard_blockers
        assert "scope_risk" in report.hard_blockers
        assert "staging_risk" in report.hard_blockers

    def test_no_scope_or_staging_risk_for_in_scope_docs(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-clean"
        ensure_run_layout(run_dir)
        spec = _task_spec(tmp_path, expected_areas=["docs"], goal="Update project docs")
        write_json_atomic(run_dir / "task-spec.json", spec.to_dict())
        (run_dir / "changed-files.txt").write_text("docs/guide.md\n", encoding="utf-8")
        (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")

        path = record_validation_report(run_dir, round_number=2)
        report = HocaValidationReport.from_json(path.read_text(encoding="utf-8"))

        assert report.scope_risk is False
        assert report.staging_risk is False
        assert report.hard_blockers == []


class TestValidationStatusIntegration:
    def test_build_validation_status_includes_scope_risk_blocker(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-scope"
        ensure_run_layout(run_dir)
        spec = _task_spec(tmp_path, expected_areas=["src/feature"], goal="Implement feature")
        write_json_atomic(run_dir / "task-spec.json", spec.to_dict())
        (run_dir / "changed-files.txt").write_text(".github/workflows/ci.yml\n", encoding="utf-8")
        (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")

        validation = build_validation_status_from_run_dir(run_dir)

        assert "scope_risk" in validation.hard_blockers
        assert "staging_risk" in validation.hard_blockers

    def test_manager_arbitration_uses_scope_risk_for_repair(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-arb"
        ensure_run_layout(run_dir)
        spec = _task_spec(tmp_path, expected_areas=["src"], goal="Small fix")
        write_json_atomic(run_dir / "task-spec.json", spec.to_dict())
        (run_dir / "changed-files.txt").write_text("infra/terraform/main.tf\n", encoding="utf-8")
        (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")

        validation = build_validation_status_from_run_dir(run_dir)
        decision = arbitrate(review=_review(), validation=validation, max_total_rounds=3)

        assert "scope_risk" in validation.hard_blockers
        assert decision.decision == "repair_required"
        assert "scope_risk" in decision.next_worker_brief or "validation" in (
            decision.next_worker_brief or ""
        ).lower()


class TestSummarizeValidationReport:
    def test_pr_body_includes_structured_validation_fields(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-summary"
        ensure_run_layout(run_dir)
        record_validation_report(run_dir, round_number=1)
        report_path = validation_report_path(run_dir, 1)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "tests_passed": False,
                "test_failure_type": "current_task",
                "scope_risk": True,
                "staging_risk": True,
                "hard_blockers": ["test_failure", "scope_risk"],
            }
        )
        write_json_atomic(report_path, payload)

        fragments = summarize_run_for_pr_body(run_dir, task="Repair validation")

        assert "Tests passed" in fragments["validation"]
        assert "Failure type" in fragments["validation"]
        assert "Scope risk" in fragments["validation"]
        assert "Staging risk" in fragments["validation"]
        assert "scope_risk" in fragments["validation"]


def test_assess_validation_risks_respects_justification(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-justified"
    ensure_run_layout(run_dir)
    spec = _task_spec(tmp_path, expected_areas=["src"], goal="Bump dependency")
    write_json_atomic(run_dir / "task-spec.json", spec.to_dict())
    (run_dir / "staging-justification.txt").write_text(
        "package-lock.json: dependency lockfile updated by package manager.\n",
        encoding="utf-8",
    )

    risk = assess_validation_risks(run_dir, ["package-lock.json"])

    assert risk.staging_risk is False
    assert risk.staging_risk_files == ()
    assert is_infrastructure_file("infra/terraform/main.tf")
