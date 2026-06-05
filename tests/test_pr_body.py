from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoca.pr_body import (
    _sanitize_pr_text,
    format_hoca_review_notes_fragment,
    format_run_context_fragment,
    format_task_spec_fragment,
    human_attention_required_for_run,
    is_draft_pr_run,
    summarize_pr_body_fragments,
    unresolved_findings_for_run,
)
from hoca.run_layout import ensure_run_layout
from hoca.run_state import optional_report_path, write_json_atomic

MAC_HOME = "/" + "Users/alice"


def _task_spec_payload(tmp_path: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-pr-body",
        "repo_root": str(tmp_path),
        "base_branch": "main",
        "task_branch": "hoca/run-pr-body",
        "issue_id": None,
        "raw_request": "Add widget",
        "goal": "Add a reusable widget component",
        "non_goals": ["Redesign the dashboard"],
        "expected_areas": ["src/components"],
        "acceptance_criteria": ["Widget renders", "Tests pass"],
        "test_commands": ["pnpm test"],
        "risk_level": "low",
        "requires_human_approval": False,
        "max_total_rounds": 3,
        "models": {
            "manager": "manager",
            "worker": "worker",
            "reviewer": "reviewer",
            "fallback": "fallback",
        },
        "sandbox": {"enabled": True, "network_mode": "offline"},
    }
    payload.update(overrides)
    return payload


def _review_report(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-pr-body",
        "round": 1,
        "role": "reviewer",
        "verdict": "LGTM",
        "findings": [],
        "pr_notes": {"summary": ["Ship-ready"], "known_followups": []},
    }
    payload.update(overrides)
    return payload


def _manager_decision(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-pr-body",
        "round": 1,
        "decision": "proceed_to_pr",
        "accepted_findings": [],
        "rejected_findings": [],
        "downgraded_to_pr_notes": [],
        "reasoning": ["No material findings remain; proceed to PR."],
        "next_worker_brief": None,
        "human_attention_required": False,
    }
    payload.update(overrides)
    return payload


def _finding(
    finding_id: str,
    *,
    severity: str = "low",
    category: str = "correctness",
    required_fix: str | None = "Tighten comparison",
) -> dict[str, object]:
    if severity == "nit" and category == "correctness":
        category = "style"
    return {
        "schema_version": 1,
        "id": finding_id,
        "severity": severity,
        "category": category,
        "file": "src/widget.ts",
        "summary": f"Finding {finding_id}",
        "required_fix": required_fix,
    }


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run-pr-body"
    ensure_run_layout(path)
    write_json_atomic(path / "task-spec.json", _task_spec_payload(tmp_path))
    (path / "tests-summary.md").write_text(
        "# Validation\n\n- pnpm test: passed\n", encoding="utf-8"
    )
    (path / "changed-files.txt").write_text("src/widget.ts\n", encoding="utf-8")
    (path / "risk-notes.txt").write_text("Low rollout risk.\n", encoding="utf-8")
    write_json_atomic(
        optional_report_path(path, "review_report", round_number=1),
        _review_report(),
    )
    write_json_atomic(
        optional_report_path(path, "manager_decision", round_number=1),
        _manager_decision(),
    )
    return path


def test_task_spec_fragment_includes_goal_and_acceptance(run_dir: Path) -> None:
    fragment = format_task_spec_fragment(run_dir, task_oneline="Add widget")
    assert "reusable widget component" in fragment
    assert "Acceptance criteria" in fragment
    assert "Widget renders" in fragment


def test_review_notes_include_verdict_and_downgraded_followups(run_dir: Path) -> None:
    write_json_atomic(
        optional_report_path(run_dir, "review_report", round_number=1),
        _review_report(
            findings=[_finding("F1", severity="nit", required_fix=None)],
            pr_notes={
                "summary": ["Approved with nits"],
                "known_followups": ["Rename test for clarity"],
            },
        ),
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=1),
        _manager_decision(
            downgraded_to_pr_notes=["F1"],
            human_attention_required=True,
        ),
    )

    fragment = format_hoca_review_notes_fragment(run_dir)

    assert "**Reviewer verdict**: LGTM" in fragment
    assert "Downgraded PR tech debt" in fragment
    assert "F1" in fragment
    assert "Rename test for clarity" in fragment


def test_review_notes_document_rejected_and_fixed_findings(run_dir: Path) -> None:
    write_json_atomic(
        optional_report_path(run_dir, "review_report", round_number=1),
        _review_report(
            verdict="fix_required",
            findings=[_finding("F1"), _finding("F2", severity="nit", required_fix=None)],
        ),
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=1),
        _manager_decision(
            round=1,
            decision="repair_required",
            accepted_findings=["F1"],
            rejected_findings=["F2"],
            next_worker_brief="Fix only finding F1.",
        ),
    )
    write_json_atomic(
        optional_report_path(run_dir, "review_report", round_number=2),
        _review_report(round=2, verdict="LGTM", findings=[]),
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=2),
        _manager_decision(round=2),
    )

    fragment = format_hoca_review_notes_fragment(run_dir)

    assert "Accepted findings fixed" in fragment
    assert "F1" in fragment
    assert "intentionally not fixed" in fragment
    assert "F2" in fragment
    assert "rejected by manager" in fragment


def test_draft_pr_notes_are_unmistakable(run_dir: Path) -> None:
    write_json_atomic(
        optional_report_path(run_dir, "review_report", round_number=3),
        _review_report(
            round=3,
            verdict="fix_required",
            findings=[_finding("F1", severity="medium")],
        ),
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=3),
        _manager_decision(
            round=3,
            decision="draft_pr_with_blockers",
            accepted_findings=["F1"],
            human_attention_required=True,
        ),
    )
    (run_dir / "draft-pr-with-blockers.flag").write_text(
        json.dumps({"accepted_findings": ["F1"]}) + "\n",
        encoding="utf-8",
    )

    assert is_draft_pr_run(run_dir) is True
    fragment = format_hoca_review_notes_fragment(run_dir)
    assert "DRAFT PR" in fragment
    assert "Residual medium findings" in fragment
    assert "F1" in fragment

    context = format_run_context_fragment(run_dir)
    assert "draft PR" in context
    assert "Human review required before merge**: yes" in context


def test_summarize_pr_body_fragments_include_new_sections(run_dir: Path) -> None:
    fragments = summarize_pr_body_fragments(run_dir, task="Add widget")

    assert "task-spec" in fragments
    assert "hoca-review-notes" in fragments
    assert "run-context" in fragments
    assert "Sandbox mode" in fragments["run-context"]
    assert "passed" in fragments["validation"]


def test_summarize_omits_secret_like_paths_from_changes(run_dir: Path) -> None:
    (run_dir / "changed-files.txt").write_text(".env\nsrc/widget.ts\n", encoding="utf-8")
    fragments = summarize_pr_body_fragments(run_dir, task="Add widget")
    assert ".env" not in fragments["changes"]
    assert "src/widget.ts" in fragments["changes"]


def test_human_attention_required_for_run_uses_manager_and_task_spec(run_dir: Path) -> None:
    write_json_atomic(
        run_dir / "task-spec.json",
        _task_spec_payload(run_dir.parent, requires_human_approval=True),
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=1),
        _manager_decision(human_attention_required=False),
    )

    assert human_attention_required_for_run(run_dir) is True


def test_unresolved_findings_for_run_collects_open_and_downgraded_findings(
    run_dir: Path,
) -> None:
    write_json_atomic(
        optional_report_path(run_dir, "review_report", round_number=1),
        _review_report(
            verdict="fix_required",
            findings=[_finding("F1"), _finding("F2", severity="low", required_fix=None)],
        ),
    )
    write_json_atomic(
        optional_report_path(run_dir, "manager_decision", round_number=1),
        _manager_decision(
            decision="repair_required",
            accepted_findings=["F1"],
            downgraded_to_pr_notes=["F2"],
            next_worker_brief="Fix F1 only.",
        ),
    )

    unresolved = unresolved_findings_for_run(run_dir)
    assert [finding.id for finding in unresolved] == ["F1", "F2"]


# ---------------------------------------------------------------------------
# Path sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (f"{MAC_HOME}/workspace/project", "<local-path>"),
        ("/home/runner/work/repo", "<local-path>"),
        ("/root/.config/app", "<local-path>"),
        ("/private/var/folders/tmp/abc123", "<local-path>"),
        ("/var/folders/89/abc123/T/tmp.txt", "<local-path>"),
        ("no path here", "no path here"),
        ("relative/path/only", "relative/path/only"),
        # Lines that are only "Label: <path>" are dropped entirely (no useful content).
        (f"Project: {MAC_HOME}/proj/.hoca-runtime/worktrees/run-123", ""),
        (f"- **Project**: {MAC_HOME}/proj", ""),
        (f"Target repository: {MAC_HOME}/proj", ""),
    ],
)
def test_sanitize_pr_text(raw: str, expected: str) -> None:
    assert _sanitize_pr_text(raw) == expected


def test_task_spec_fragment_redacts_local_paths_from_goal(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-sanitize"
    ensure_run_layout(run_dir)
    write_json_atomic(
        run_dir / "task-spec.json",
        _task_spec_payload(
            tmp_path,
            goal=(
                "Add typed env config.\n\n"
                f"Target repository: {MAC_HOME}/workspace/projects/sample-project\n\n"
                "Scope: replace env.ts"
            ),
        ),
    )
    fragment = format_task_spec_fragment(run_dir, task_oneline="Add typed env config.")
    assert "/" + "Users/" not in fragment
    assert "Add typed env config." in fragment


def test_summarize_pr_body_fragments_redacts_local_paths(run_dir: Path) -> None:
    (run_dir / "tests-summary.md").write_text(
        "# Validation\n\n"
        "- **Status**: passed\n"
        f"- **Project**: {MAC_HOME}/workspace/projects/sample-project/.hoca-runtime/worktrees/run-123\n",
        encoding="utf-8",
    )
    task_with_path = (
        "Add typed env config.\n\n"
        f"Target repository: {MAC_HOME}/workspace/projects/sample-project\n\n"
        "Scope: replace env.ts"
    )
    write_json_atomic(
        run_dir / "task-spec.json",
        _task_spec_payload(run_dir.parent, goal=task_with_path),
    )
    fragments = summarize_pr_body_fragments(run_dir, task=task_with_path)
    for key, value in fragments.items():
        assert "/" + "Users/" not in value, f"Local path leaked in fragment '{key}'"
        assert "/home/" not in value, f"Local path leaked in fragment '{key}'"


def test_summarize_pr_body_fragments_uses_only_first_task_line_in_summary(
    run_dir: Path,
) -> None:
    task = (
        "Add typed env config.\n\n"
        f"Target repository: {MAC_HOME}/workspace/projects/sample-project\n\n"
        "Scope: replace env.ts"
    )
    fragments = summarize_pr_body_fragments(run_dir, task=task)
    assert "Target repository" not in fragments["summary"]
    assert "Add typed env config." in fragments["summary"]
