from __future__ import annotations

import json
from typing import Any

import pytest

from hoca.contracts import (
    HocaAttemptReport,
    HocaManagerDecision,
    HocaModelConfig,
    HocaModelPool,
    HocaReviewFinding,
    HocaReviewReport,
    HocaRoleModelSelection,
    HocaRunFinalState,
    HocaSandboxPolicy,
    HocaTaskSpec,
)


def sample_role_selection() -> HocaRoleModelSelection:
    return HocaRoleModelSelection(
        manager="local-coder",
        worker="local-coder",
        reviewer="reviewer-strong",
        fallback="local-fast",
    )


def sample_sandbox_policy() -> HocaSandboxPolicy:
    return HocaSandboxPolicy(enabled=True, network_mode="offline")


def sample_task_spec(**overrides: Any) -> HocaTaskSpec:
    defaults: dict[str, Any] = {
        "run_id": "run-123",
        "repo_root": "/repo",
        "base_branch": "main",
        "task_branch": "codex/example",
        "issue_id": None,
        "raw_request": "Please implement the task",
        "goal": "Implement the task",
        "non_goals": ["Do not refactor unrelated code"],
        "expected_areas": ["hoca/contracts.py"],
        "acceptance_criteria": ["Contracts serialize"],
        "test_commands": ["pytest tests/test_contracts.py"],
        "risk_level": "low",
        "requires_human_approval": True,
        "max_total_rounds": 3,
        "models": sample_role_selection(),
        "sandbox": sample_sandbox_policy(),
    }
    defaults.update(overrides)
    return HocaTaskSpec(**defaults)


def test_task_spec_serializes_to_deterministic_json() -> None:
    spec = sample_task_spec()

    raw = spec.to_json()

    assert raw.endswith("\n")
    assert raw.index('"acceptance_criteria"') < raw.index('"base_branch"')
    parsed = json.loads(raw)
    assert parsed["sandbox"] == {
        "enabled": True,
        "network_mode": "offline",
        "schema_version": 1,
    }
    assert parsed["raw_request"] == "Please implement the task"
    assert parsed["max_total_rounds"] == 3


def test_task_spec_deserializes_from_json() -> None:
    spec = HocaTaskSpec.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-123",
                "repo_root": "/repo",
                "base_branch": "main",
                "task_branch": "codex/example",
                "issue_id": "42",
                "raw_request": "Fix the bug in auth",
                "goal": "Implement the task",
                "non_goals": [],
                "expected_areas": ["hoca"],
                "acceptance_criteria": ["passes tests"],
                "test_commands": ["pytest"],
                "risk_level": "medium",
                "requires_human_approval": True,
                "max_total_rounds": 3,
                "models": sample_role_selection().to_dict(),
                "sandbox": sample_sandbox_policy().to_dict(),
            }
        )
    )

    assert spec.issue_id == "42"
    assert spec.raw_request == "Fix the bug in auth"
    assert spec.models.reviewer == "reviewer-strong"
    assert spec.sandbox.network_mode == "offline"


def test_task_spec_max_total_rounds_defaults_to_3() -> None:
    data = {
        "schema_version": 1,
        "run_id": "run-456",
        "repo_root": "/repo",
        "base_branch": "main",
        "task_branch": "feat/test",
        "issue_id": None,
        "raw_request": "Some task",
        "goal": "Do something",
        "non_goals": [],
        "expected_areas": [],
        "acceptance_criteria": [],
        "test_commands": [],
        "risk_level": "low",
        "requires_human_approval": False,
        "models": sample_role_selection().to_dict(),
        "sandbox": sample_sandbox_policy().to_dict(),
    }

    spec = HocaTaskSpec.from_dict(data)

    assert spec.max_total_rounds == 3


def test_task_spec_rejects_invalid_risk_level() -> None:
    data = {
        "schema_version": 1,
        "run_id": "run-789",
        "repo_root": "/repo",
        "base_branch": "main",
        "task_branch": "feat/test",
        "issue_id": None,
        "raw_request": "Some task",
        "goal": "Do something",
        "non_goals": [],
        "expected_areas": [],
        "acceptance_criteria": [],
        "test_commands": [],
        "risk_level": "extreme",
        "requires_human_approval": False,
        "models": sample_role_selection().to_dict(),
        "sandbox": sample_sandbox_policy().to_dict(),
    }

    with pytest.raises(ValueError, match="risk_level must be one of"):
        HocaTaskSpec.from_dict(data)


def test_task_spec_raw_request_preserves_original_human_input() -> None:
    spec = sample_task_spec(
        raw_request="fix the auth bug pls",
        goal="Fix token expiry validation in the auth API",
    )

    assert spec.raw_request == "fix the auth bug pls"
    assert spec.goal == "Fix token expiry validation in the auth API"

    parsed = HocaTaskSpec.from_json(spec.to_json())
    assert parsed.raw_request == spec.raw_request
    assert parsed.goal == spec.goal


def test_task_spec_repo_relative_paths_in_expected_areas() -> None:
    spec = sample_task_spec(
        expected_areas=["apps/api/src/auth", "apps/api/tests/auth"],
    )

    assert all(not area.startswith("/") for area in spec.expected_areas)

    parsed = HocaTaskSpec.from_json(spec.to_json())
    assert parsed.expected_areas == spec.expected_areas


def test_unknown_future_fields_do_not_crash_readback() -> None:
    data = sample_role_selection().to_dict()
    data["future_field"] = {"ignored": True}

    selection = HocaRoleModelSelection.from_dict(data)

    assert selection.manager == "local-coder"


def test_missing_required_fields_raise_value_error() -> None:
    data = sample_role_selection().to_dict()
    del data["worker"]

    with pytest.raises(ValueError, match="worker"):
        HocaRoleModelSelection.from_dict(data)


def test_attempt_report_round_trips_json() -> None:
    report = HocaAttemptReport(
        run_id="run-123",
        round=1,
        role="worker",
        status="completed",
        changed_files=["hoca/contracts.py"],
        summary=["Implemented contracts"],
        commands_run=["pytest"],
        tests_run=["pytest tests/test_contracts.py"],
        known_risks=[],
        blocked_reason=None,
        artifact_paths={"openhands_output": "worker.json", "monitor_result": "monitor.json"},
    )

    assert HocaAttemptReport.from_json(report.to_json()) == report


def test_failed_attempt_report_can_be_read_without_changed_files() -> None:
    report = HocaAttemptReport.from_dict(
        {
            "schema_version": 1,
            "run_id": "run-456",
            "round": 2,
            "role": "worker",
            "status": "failed",
            "changed_files": [],
            "summary": ["OpenHands exited non-zero before making changes"],
            "commands_run": ["openhands --headless --task ..."],
            "tests_run": [],
            "known_risks": ["Implementation did not complete"],
            "blocked_reason": "openhands_failed",
            "artifact_paths": {
                "openhands_output": ".hoca-runtime/runs/run-456/openhands-output.jsonl",
                "monitor_result": ".hoca-runtime/runs/run-456/monitor-result.json",
            },
        }
    )

    assert report.status == "failed"
    assert report.changed_files == []
    assert report.blocked_reason == "openhands_failed"


def test_attempt_report_rejects_invalid_status() -> None:
    data = {
        "schema_version": 1,
        "run_id": "run-789",
        "round": 1,
        "role": "worker",
        "status": "running",
        "changed_files": [],
        "summary": [],
        "commands_run": [],
        "tests_run": [],
        "known_risks": [],
        "blocked_reason": None,
        "artifact_paths": {"openhands_output": "worker.json", "monitor_result": "monitor.json"},
    }

    with pytest.raises(ValueError, match="status must be one of"):
        HocaAttemptReport.from_dict(data)


def test_attempt_report_rejects_non_worker_role() -> None:
    data = {
        "schema_version": 1,
        "run_id": "run-789",
        "round": 1,
        "role": "manager",
        "status": "completed",
        "changed_files": [],
        "summary": [],
        "commands_run": [],
        "tests_run": [],
        "known_risks": [],
        "blocked_reason": None,
        "artifact_paths": {"openhands_output": "worker.json", "monitor_result": "monitor.json"},
    }

    with pytest.raises(ValueError, match="role must be one of"):
        HocaAttemptReport.from_dict(data)


def test_attempt_report_artifact_paths_must_point_to_raw_log_files() -> None:
    missing_artifact_data = {
        "schema_version": 1,
        "run_id": "run-789",
        "round": 1,
        "role": "worker",
        "status": "blocked",
        "changed_files": [],
        "summary": ["Blocked by monitor"],
        "commands_run": [],
        "tests_run": [],
        "known_risks": ["Secret access attempt detected"],
        "blocked_reason": "secret_access",
        "artifact_paths": {"openhands_output": "openhands-output.jsonl"},
    }

    with pytest.raises(ValueError, match="Missing required artifact path"):
        HocaAttemptReport.from_dict(missing_artifact_data)

    unsafe_artifact_data = {
        **missing_artifact_data,
        "artifact_paths": {
            "openhands_output": "raw log contents\nTOKEN=value",
            "monitor_result": ".env",
        },
    }
    with pytest.raises(ValueError, match="single line|secret-like"):
        HocaAttemptReport.from_dict(unsafe_artifact_data)


def test_review_report_round_trips_json() -> None:
    report = HocaReviewReport(
        run_id="run-123",
        round=1,
        role="reviewer",
        verdict="fix_required",
        findings=[
            HocaReviewFinding(
                id="F1",
                severity="medium",
                category="test",
                file="tests/test_contracts.py",
                summary="Missing coverage",
                required_fix="Add round-trip tests",
            )
        ],
        pr_notes={"summary": ["Needs tests"], "known_followups": []},
    )

    parsed = HocaReviewReport.from_json(report.to_json())

    assert parsed == report
    assert parsed.findings[0].required_fix == "Add round-trip tests"


def _manager_decision(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "schema_version": 1,
        "run_id": "run-123",
        "round": 1,
        "decision": "repair_required",
        "accepted_findings": ["F1"],
        "rejected_findings": [],
        "downgraded_to_pr_notes": [],
        "reasoning": ["F1 affects correctness and must be fixed"],
        "next_worker_brief": "Fix only F1",
        "human_attention_required": False,
    }
    defaults.update(overrides)
    return defaults


def test_manager_decision_round_trips_json() -> None:
    decision = HocaManagerDecision(
        run_id="run-123",
        round=1,
        decision="repair_required",
        accepted_findings=["F1"],
        rejected_findings=[],
        downgraded_to_pr_notes=[],
        reasoning=["Tests are missing"],
        next_worker_brief="Add tests",
        human_attention_required=False,
    )

    assert HocaManagerDecision.from_json(decision.to_json()) == decision


# --- HocaManagerDecision acceptance criteria ---


def test_manager_decision_reject_inconsequential_findings() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="proceed_to_pr",
            accepted_findings=[],
            rejected_findings=["F2"],
            downgraded_to_pr_notes=["F3"],
            reasoning=[
                "F2 is a style preference, not a correctness issue",
                "F3 is low-priority cleanup, moved to PR notes",
            ],
            next_worker_brief=None,
        )
    )

    assert decision.decision == "proceed_to_pr"
    assert "F2" in decision.rejected_findings
    assert "F3" in decision.downgraded_to_pr_notes


def test_manager_decision_accept_required_findings() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="repair_required",
            accepted_findings=["F1", "F2"],
            rejected_findings=[],
            reasoning=["F1 is a correctness bug", "F2 is a missing test"],
            next_worker_brief="Fix F1: correct the UTC comparison. Fix F2: add expiry test.",
        )
    )

    assert decision.decision == "repair_required"
    assert decision.accepted_findings == ["F1", "F2"]
    assert decision.next_worker_brief is not None
    assert "F1" in decision.next_worker_brief


def test_manager_decision_focused_repair_brief() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="repair_required",
            accepted_findings=["R1"],
            rejected_findings=["R2"],
            downgraded_to_pr_notes=[],
            reasoning=[
                "R1 affects correctness and must be fixed",
                "R2 does not affect quality enough to block this PR",
            ],
            next_worker_brief="Fix only R1. Do not rename tests for R2. Leave R2 as PR follow-up.",
        )
    )

    assert decision.next_worker_brief is not None
    assert "R1" in decision.next_worker_brief
    assert "R2" in decision.next_worker_brief


def test_manager_decision_block_on_hard_blockers() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="blocked",
            accepted_findings=["F1"],
            rejected_findings=[],
            reasoning=["F1 is a critical security regression that cannot be shipped"],
            next_worker_brief=None,
            human_attention_required=True,
        )
    )

    assert decision.decision == "blocked"
    assert decision.human_attention_required is True
    assert decision.next_worker_brief is None


def test_manager_decision_proceed_to_pr() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="proceed_to_pr",
            accepted_findings=[],
            rejected_findings=[],
            downgraded_to_pr_notes=[],
            reasoning=["All findings resolved, tests pass"],
            next_worker_brief=None,
            human_attention_required=False,
        )
    )

    assert decision.decision == "proceed_to_pr"
    assert decision.human_attention_required is False


def test_manager_decision_draft_pr_with_blockers() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="draft_pr_with_blockers",
            accepted_findings=["F1"],
            rejected_findings=[],
            downgraded_to_pr_notes=["F2"],
            reasoning=[
                "Round 3 reached with medium residual findings",
                "No hard blockers remain",
                "F2 moved to PR notes as low-priority cleanup",
            ],
            next_worker_brief=None,
            human_attention_required=True,
        )
    )

    assert decision.decision == "draft_pr_with_blockers"
    assert decision.human_attention_required is True


def test_manager_decision_rejects_invalid_decision() -> None:
    with pytest.raises(ValueError, match="decision must be one of"):
        HocaManagerDecision.from_dict(
            _manager_decision(decision="auto_merge")
        )


def test_manager_decision_rejects_invalid_round() -> None:
    with pytest.raises(ValueError, match="round must be greater than or equal to 1"):
        HocaManagerDecision.from_dict(
            _manager_decision(round=0)
        )


def test_manager_decision_repair_required_needs_worker_brief() -> None:
    with pytest.raises(ValueError, match="next_worker_brief is required"):
        HocaManagerDecision.from_dict(
            _manager_decision(
                decision="repair_required",
                next_worker_brief=None,
            )
        )


def test_manager_decision_mixed_finding_disposition() -> None:
    decision = HocaManagerDecision.from_dict(
        _manager_decision(
            decision="repair_required",
            accepted_findings=["F1"],
            rejected_findings=["F2"],
            downgraded_to_pr_notes=["F3"],
            reasoning=[
                "F1 is a correctness bug that must be fixed",
                "F2 is an inconsequential style preference",
                "F3 is low-priority cleanup for a future PR",
            ],
            next_worker_brief="Fix F1 only. Do not address F2 or F3.",
        )
    )

    assert len(decision.accepted_findings) == 1
    assert len(decision.rejected_findings) == 1
    assert len(decision.downgraded_to_pr_notes) == 1
    assert len(decision.reasoning) == 3


def test_manager_decision_missing_required_field() -> None:
    data = _manager_decision()
    del data["reasoning"]

    with pytest.raises(ValueError, match="reasoning"):
        HocaManagerDecision.from_dict(data)


def test_manager_decision_unknown_fields_do_not_crash() -> None:
    data = _manager_decision()
    data["future_manager_field"] = "some_value"

    decision = HocaManagerDecision.from_dict(data)
    assert decision.decision == "repair_required"


def _finding(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "F1",
        "severity": "medium",
        "category": "correctness",
        "file": "src/main.py",
        "summary": "Logic error in handler",
        "required_fix": "Fix the condition",
    }
    defaults.update(overrides)
    return defaults


def _review_report(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "schema_version": 1,
        "run_id": "run-123",
        "round": 1,
        "role": "reviewer",
        "verdict": "fix_required",
        "findings": [_finding()],
        "pr_notes": {"summary": ["Needs fix"], "known_followups": []},
    }
    defaults.update(overrides)
    return defaults


# --- HocaReviewReport acceptance criteria ---


def test_review_report_expresses_blocking_findings() -> None:
    report = HocaReviewReport.from_dict(
        _review_report(
            verdict="fix_required",
            findings=[
                _finding(id="F1", severity="high", category="correctness", required_fix="Fix it"),
            ],
        )
    )

    assert report.verdict == "fix_required"
    assert report.findings[0].severity == "high"
    assert report.findings[0].required_fix is not None


def test_review_report_expresses_non_blocking_findings() -> None:
    report = HocaReviewReport.from_dict(
        _review_report(
            verdict="LGTM",
            findings=[
                _finding(
                    id="F1", severity="low", category="maintainability", required_fix=None
                ),
            ],
        )
    )

    assert report.verdict == "LGTM"
    assert report.findings[0].severity == "low"
    assert report.findings[0].required_fix is None


def test_review_report_low_priority_findings_downgraded_to_pr_notes() -> None:
    report = HocaReviewReport.from_dict(
        _review_report(
            verdict="LGTM",
            findings=[
                _finding(id="F1", severity="nit", category="style", required_fix=None),
            ],
            pr_notes={
                "summary": ["Minor style nit"],
                "known_followups": ["Consider renaming variable for clarity"],
            },
        )
    )

    assert report.verdict == "LGTM"
    assert report.findings[0].severity == "nit"
    assert len(report.pr_notes["known_followups"]) == 1


def test_review_report_lgtm_with_non_blocking_followups() -> None:
    report = HocaReviewReport.from_dict(
        _review_report(
            verdict="LGTM",
            findings=[],
            pr_notes={
                "summary": ["Approved with minor follow-ups"],
                "known_followups": [
                    "Add doc comment to exported function",
                    "Consider extracting helper",
                ],
            },
        )
    )

    assert report.verdict == "LGTM"
    assert len(report.findings) == 0
    assert len(report.pr_notes["known_followups"]) == 2


def test_review_report_blocked_verdict() -> None:
    report = HocaReviewReport.from_dict(
        _review_report(
            verdict="blocked",
            findings=[
                _finding(id="F1", severity="critical", category="security",
                         summary="Credential leak", required_fix="Remove leaked secret"),
            ],
        )
    )

    assert report.verdict == "blocked"
    assert report.findings[0].severity == "critical"
    assert report.findings[0].category == "security"


def test_review_finding_rejects_security_nit() -> None:
    with pytest.raises(ValueError, match="Security findings must have severity"):
        HocaReviewFinding.from_dict(
            _finding(severity="nit", category="security")
        )


def test_review_finding_rejects_security_low() -> None:
    with pytest.raises(ValueError, match="Security findings must have severity"):
        HocaReviewFinding.from_dict(
            _finding(severity="low", category="security")
        )


def test_review_finding_allows_security_medium() -> None:
    finding = HocaReviewFinding.from_dict(
        _finding(severity="medium", category="security")
    )
    assert finding.severity == "medium"
    assert finding.category == "security"


def test_review_finding_rejects_correctness_nit() -> None:
    with pytest.raises(ValueError, match="Correctness findings cannot have severity"):
        HocaReviewFinding.from_dict(
            _finding(severity="nit", category="correctness")
        )


def test_review_finding_allows_correctness_low() -> None:
    finding = HocaReviewFinding.from_dict(
        _finding(severity="low", category="correctness")
    )
    assert finding.severity == "low"


def test_review_finding_rejects_invalid_severity() -> None:
    with pytest.raises(ValueError, match="severity must be one of"):
        HocaReviewFinding.from_dict(
            _finding(severity="extreme")
        )


def test_review_finding_rejects_invalid_category() -> None:
    with pytest.raises(ValueError, match="category must be one of"):
        HocaReviewFinding.from_dict(
            _finding(category="performance")
        )


def test_review_finding_supports_tooling_category() -> None:
    finding = HocaReviewFinding.from_dict(
        _finding(category="tooling", severity="low")
    )
    assert finding.category == "tooling"


def test_review_finding_supports_environment_category() -> None:
    finding = HocaReviewFinding.from_dict(
        _finding(category="environment", severity="medium")
    )
    assert finding.category == "environment"


def test_review_report_rejects_invalid_verdict() -> None:
    with pytest.raises(ValueError, match="verdict must be one of"):
        HocaReviewReport.from_dict(
            _review_report(verdict="approved")
        )


def test_review_report_rejects_non_reviewer_role() -> None:
    with pytest.raises(ValueError, match="role must be one of"):
        HocaReviewReport.from_dict(
            _review_report(role="worker")
        )


def test_review_report_rejects_invalid_round() -> None:
    with pytest.raises(ValueError, match="round must be greater than or equal to 1"):
        HocaReviewReport.from_dict(
            _review_report(round=0)
        )


def test_review_report_mixed_severity_findings() -> None:
    report = HocaReviewReport.from_dict(
        _review_report(
            verdict="fix_required",
            findings=[
                _finding(id="F1", severity="critical", category="security",
                         required_fix="Remove credential"),
                _finding(id="F2", severity="medium", category="test",
                         required_fix="Add test"),
                _finding(id="F3", severity="nit", category="style",
                         required_fix=None),
            ],
        )
    )

    assert len(report.findings) == 3
    severities = [f.severity for f in report.findings]
    assert "critical" in severities
    assert "nit" in severities


def test_model_pool_serializes_with_safe_redaction() -> None:
    pool = HocaModelPool(
        models=[
            HocaModelConfig(
                name="local-coder",
                model="ollama/qwen-14b-pro",
                base_url="http://127.0.0.1:11434",
                api_key="secret",
            )
        ],
        roles=sample_role_selection(),
    )

    assert HocaModelPool.from_json(pool.to_json()) == pool
    assert pool.safe_dict()["models"][0]["api_key"] == "***"


def test_run_final_state_round_trips_json() -> None:
    state = HocaRunFinalState(
        run_id="run-123",
        status="pr_opened",
        summary=["Run complete"],
        changed_files=["hoca/contracts.py"],
        tests_run=["pytest"],
        attempt_reports=["attempt-1.json"],
        review_reports=["review-1.json"],
        manager_decisions=["decision-1.json"],
        pr_url="https://example.test/pr/1",
        completed_at="2026-05-19T18:00:00Z",
        blocked_reason=None,
    )

    assert HocaRunFinalState.from_json(state.to_json()) == state
