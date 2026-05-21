from __future__ import annotations

import json
from pathlib import Path

from hoca.contracts import HocaReviewReport, HocaSandboxPolicy, HocaTaskSpec
from hoca.run_artifacts import (
    init_run_layout,
    record_final_state,
    record_manager_decision,
    record_validation_report,
    record_worker_attempt,
)
from hoca.run_layout import (
    RUN_SUBDIRS,
    ensure_run_layout,
    final_state_path,
    manager_decision_path,
    review_report_path,
    sandbox_policy_path,
    task_spec_path,
    validation_report_path,
    worker_attempt_path,
)
from hoca.run_state import (
    current_round,
    list_round_artifact_paths,
    read_optional_json,
    write_json_atomic,
)


def test_ensure_run_layout_creates_subdirectories(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    ensure_run_layout(run_dir)
    assert run_dir.is_dir()
    for subdir in RUN_SUBDIRS:
        assert (run_dir / subdir).is_dir()


def test_round_artifact_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-2"
    ensure_run_layout(run_dir)
    assert worker_attempt_path(run_dir, 2) == run_dir / "attempts" / "worker-attempt-2.json"
    assert review_report_path(run_dir, 1) == run_dir / "reviews" / "review-report-1.json"
    assert manager_decision_path(run_dir, 3) == run_dir / "decisions" / "manager-decision-3.json"
    assert validation_report_path(run_dir, 1) == run_dir / "validation" / "validation-report-1.json"
    assert task_spec_path(run_dir) == run_dir / "task-spec.json"
    assert sandbox_policy_path(run_dir) == run_dir / "sandbox-policy.json"
    assert final_state_path(run_dir) == run_dir / "final-state.json"


def test_init_run_layout_writes_task_spec_and_sandbox_policy(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-3"
    init_run_layout(
        run_dir,
        run_id="run-3",
        repo_root=str(tmp_path),
        base_branch="main",
        task_branch="feat/example",
        raw_request="Update README",
        issue_id=None,
        max_total_rounds=3,
        sandbox_enabled=True,
        sandbox_network_mode="offline",
    )

    spec = HocaTaskSpec.from_json(task_spec_path(run_dir).read_text(encoding="utf-8"))
    assert spec.run_id == "run-3"
    assert spec.goal == "Update README"
    assert spec.max_total_rounds == 3

    sandbox = HocaSandboxPolicy.from_json(
        sandbox_policy_path(run_dir).read_text(encoding="utf-8")
    )
    assert sandbox.enabled is True
    assert sandbox.network_mode == "offline"


def test_record_worker_and_validation_reports(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-4"
    ensure_run_layout(run_dir)
    (run_dir / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
    (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")
    (run_dir / "tests-summary.md").write_text(
        "# Test Summary\n\n- **Status**: passed\n", encoding="utf-8"
    )

    (run_dir / "git-status.txt").write_text(" M README.md\n", encoding="utf-8")

    worker_path = record_worker_attempt(run_dir, round_number=1, status="completed")
    validation_path = record_validation_report(run_dir, round_number=1)

    assert worker_path.is_file()
    assert validation_path.is_file()
    validation_data = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validation_data["git_status"] == ["M README.md"]
    assert validation_data["changed_files"] == ["README.md"]
    assert validation_data["secret_scan_clean"] is True
    assert validation_data["monitor_clean"] is True
    assert validation_data["tests_passed"] is True
    assert current_round(run_dir, prefix="worker-attempt-", subdir="attempts") == 1
    assert current_round(run_dir, prefix="validation-report-", subdir="validation") == 1


def test_record_manager_decision_from_review_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-5"
    ensure_run_layout(run_dir)
    (run_dir / "tests-exit-code.txt").write_text("0\n", encoding="utf-8")

    review = HocaReviewReport(
        run_id="run-5",
        round=1,
        role="reviewer",
        verdict="LGTM",
        findings=[],
        pr_notes={"summary": ["Looks good"], "known_followups": []},
    )
    write_json_atomic(review_report_path(run_dir, 1), review.to_dict())

    decision_path = record_manager_decision(run_dir, round_number=1)
    assert decision_path is not None
    assert decision_path.is_file()
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "proceed_to_pr"


def test_record_final_state_collects_artifact_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-6"
    ensure_run_layout(run_dir)
    write_json_atomic(
        run_dir / "status.json",
        {"status": "pr_created", "run_id": "run-6", "reason": "pull_request_created"},
    )
    (run_dir / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
    (run_dir / "pr-url.txt").write_text("https://example.test/pr/1\n", encoding="utf-8")
    record_worker_attempt(run_dir, round_number=1, status="completed")

    final_path = record_final_state(run_dir)
    assert final_path.is_file()
    final_data = read_optional_json(final_path)
    assert final_data is not None
    assert final_data["status"] == "pr_opened"
    assert final_data["reason"] == "pull_request_created"
    assert final_data["pr_url"] == "https://example.test/pr/1"
    assert final_data["human_attention_required"] is False
    assert final_data["unresolved_findings"] == []
    assert list_round_artifact_paths(run_dir, "attempts", "worker-attempt-")


def test_record_final_state_includes_unresolved_findings_and_human_attention(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-final-structured"
    ensure_run_layout(run_dir)
    write_json_atomic(
        run_dir / "status.json",
        {
            "status": "blocked",
            "run_id": "run-final-structured",
            "reason": "round_cap_hard_blockers",
            "human_attention_required": True,
        },
    )
    write_json_atomic(
        review_report_path(run_dir, 1),
        {
            "schema_version": 1,
            "run_id": "run-final-structured",
            "round": 1,
            "role": "reviewer",
            "verdict": "fix_required",
            "findings": [
                {
                    "schema_version": 1,
                    "id": "F1",
                    "severity": "high",
                    "category": "correctness",
                    "file": "src/app.py",
                    "summary": "Null pointer risk",
                    "required_fix": "Guard against null input",
                }
            ],
            "pr_notes": {"summary": [], "known_followups": []},
        },
    )
    write_json_atomic(
        manager_decision_path(run_dir, 1),
        {
            "schema_version": 1,
            "run_id": "run-final-structured",
            "round": 1,
            "decision": "blocked",
            "accepted_findings": ["F1"],
            "rejected_findings": [],
            "downgraded_to_pr_notes": [],
            "reasoning": ["Hard blocker remains after round cap."],
            "next_worker_brief": None,
            "human_attention_required": True,
        },
    )

    final_path = record_final_state(run_dir)
    final_data = read_optional_json(final_path)
    assert final_data is not None
    assert final_data["status"] == "blocked"
    assert final_data["reason"] == "round_cap_hard_blockers"
    assert final_data["blocked_reason"] == "round_cap_hard_blockers"
    assert final_data["human_attention_required"] is True
    assert len(final_data["unresolved_findings"]) == 1
    assert final_data["unresolved_findings"][0]["id"] == "F1"
