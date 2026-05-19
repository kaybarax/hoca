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
