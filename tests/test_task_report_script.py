from __future__ import annotations

import json
import subprocess
from pathlib import Path

from hoca.task_report import build_task_report_markdown, write_task_report
from tests.test_safe_staging_scripts import init_repo


REPORT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate-task-report.sh"


def run_report(repo: Path, run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(REPORT_SCRIPT), str(repo), str(run_dir)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_task_report_contains_required_run_fields(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/report"], cwd=tmp_path, check=True)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "issue_id": "42",
                "task": "Generate report",
                "status": "committed",
                "started_at": "2026-05-13T01:02:03Z",
                "auto_merge": "false",
                "merge_performed": False,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "changed-files.txt").write_text("README.md\nscripts/report.sh\n", encoding="utf-8")
    (run_dir / "tests-summary.md").write_text(
        "# Test Summary\n\n- **Status**: passed\n- **Command**: `pytest`\n",
        encoding="utf-8",
    )
    (run_dir / "openhands-review.txt").write_text("Looks good.\nLGTM\n", encoding="utf-8")
    (run_dir / "openhands-review-exit-code.txt").write_text("0\n", encoding="utf-8")
    (run_dir / "commit-hash.txt").write_text("abc123\n", encoding="utf-8")

    result = run_report(tmp_path, run_dir)

    assert result.returncode == 0, result.stderr
    report = (run_dir / "task-report.md").read_text(encoding="utf-8")
    assert "## HOCA Task Report" in report
    assert "### Task\nGenerate report" in report
    assert "- Run ID: run-1" in report
    assert "- Issue ID: 42" in report
    assert "- Start time: 2026-05-13T01:02:03Z" in report
    assert "- Final status: committed" in report
    assert "feat/report" in report
    assert "- README.md" in report
    assert "- scripts/report.sh" in report
    assert "- **Status**: passed" in report
    assert "- Status: LGTM" in report
    assert "- not merged" in report
    assert "`abc123`" in report


def test_task_report_prefers_structured_review_gate_over_legacy_text(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-structured-review"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "run-structured-review",
                "task": "Structured review gate",
                "status": "committed",
                "started_at": "2026-05-13T01:02:03Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "openhands-review.txt").write_text("LGTM\n", encoding="utf-8")
    reviews = run_dir / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "review-report-1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured-review",
                "round": 1,
                "role": "reviewer",
                "verdict": "blocked",
                "findings": [],
                "pr_notes": {
                    "summary": ["Reviewer could not complete review."],
                    "known_followups": [],
                },
            }
        ),
        encoding="utf-8",
    )

    result = run_report(tmp_path, run_dir)

    assert result.returncode == 0, result.stderr
    report = (run_dir / "task-report.md").read_text(encoding="utf-8")
    assert "- Status: blocked" in report


def test_task_report_links_logs_without_dumping_large_log_content(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-2"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "task": "Handle failing tests",
                "status": "failed",
                "reason": "command_failed",
                "started_at": "2026-05-13T01:02:03Z",
            }
        ),
        encoding="utf-8",
    )
    large_log = "SECRET_SHOULD_NOT_BE_DUMPED\n" * 200
    (run_dir / "tests-output.log").write_text(large_log, encoding="utf-8")
    (run_dir / "failed-command.txt").write_text("pytest\n", encoding="utf-8")

    result = run_report(tmp_path, run_dir)

    assert result.returncode == 0, result.stderr
    report = (run_dir / "task-report.md").read_text(encoding="utf-8")
    assert "- Failed command: `pytest`" in report
    assert "tests-output.log" in report
    assert "SECRET_SHOULD_NOT_BE_DUMPED" not in report


def test_task_report_includes_structured_artifacts_and_redacts_credentials(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-structured"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "run-structured",
                "task": "Structured upgrade",
                "status": "pr_created",
                "started_at": "2026-05-13T01:02:03Z",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "pr-url.txt").write_text("https://github.com/org/repo/pull/99\n", encoding="utf-8")
    (run_dir / "task-spec.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured",
                "repo_root": str(tmp_path),
                "base_branch": "main",
                "task_branch": "hoca/run-structured",
                "issue_id": None,
                "raw_request": "Add feature",
                "goal": "Add the requested feature safely",
                "non_goals": [],
                "expected_areas": [],
                "acceptance_criteria": ["Tests pass"],
                "test_commands": ["pytest"],
                "risk_level": "low",
                "requires_human_approval": True,
                "max_total_rounds": 3,
                "models": {
                    "manager": "local-fast",
                    "worker": "local-coder",
                    "reviewer": "reviewer-strong",
                    "fallback": "local-fast",
                },
                "sandbox": {"enabled": True, "network_mode": "offline"},
            }
        ),
        encoding="utf-8",
    )

    attempts = run_dir / "attempts"
    attempts.mkdir(parents=True)
    (attempts / "worker-attempt-1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured",
                "round": 1,
                "role": "worker",
                "status": "completed",
                "changed_files": ["src/feature.py"],
                "summary": ["Implemented feature", "api_key=super-secret-token"],
                "commands_run": [],
                "tests_run": ["pytest"],
                "known_risks": [],
                "blocked_reason": None,
                "artifact_paths": {
                    "openhands_output": "openhands-output.log",
                    "monitor_result": "monitor-result.json",
                },
            }
        ),
        encoding="utf-8",
    )

    reviews = run_dir / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "review-report-1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured",
                "round": 1,
                "role": "reviewer",
                "verdict": "fix_required",
                "findings": [
                    {
                        "schema_version": 1,
                        "id": "F1",
                        "severity": "medium",
                        "category": "test",
                        "file": "tests/test_feature.py",
                        "summary": "Missing edge-case coverage",
                        "required_fix": "Add invalid-input test",
                    }
                ],
                "pr_notes": {"summary": ["Needs one fix"], "known_followups": []},
            }
        ),
        encoding="utf-8",
    )

    decisions = run_dir / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "manager-decision-1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured",
                "round": 1,
                "decision": "repair_required",
                "accepted_findings": ["F1"],
                "rejected_findings": [],
                "downgraded_to_pr_notes": [],
                "reasoning": ["F1 affects test correctness and must be fixed."],
                "next_worker_brief": "Fix only F1.",
                "human_attention_required": False,
            }
        ),
        encoding="utf-8",
    )

    validation = run_dir / "validation"
    validation.mkdir(parents=True)
    (validation / "validation-report-1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured",
                "round": 1,
                "tests_passed": True,
                "test_failure_type": None,
                "git_status": [],
                "changed_files": ["src/feature.py"],
                "secret_scan_clean": True,
                "monitor_clean": True,
                "monitor_stop_reason": None,
                "hard_blockers": [],
                "scope_risk": False,
                "staging_risk": False,
                "artifact_paths": {},
            }
        ),
        encoding="utf-8",
    )

    (run_dir / "final-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-structured",
                "status": "pr_created",
                "summary": ["Worker completed implementation and manager opened a PR."],
                "changed_files": ["src/feature.py"],
                "tests_run": ["pytest"],
                "attempt_reports": ["attempts/worker-attempt-1.json"],
                "review_reports": ["reviews/review-report-1.json"],
                "manager_decisions": ["decisions/manager-decision-1.json"],
                "pr_url": "https://github.com/org/repo/pull/99",
                "completed_at": "2026-05-13T02:00:00Z",
                "blocked_reason": None,
            }
        ),
        encoding="utf-8",
    )

    report = build_task_report_markdown(tmp_path, run_dir)

    assert "### Models" in report
    assert "local-coder" in report
    assert "reviewer-strong" in report
    assert "### Worker Attempts" in report
    assert "Implemented feature" in report
    assert "super-secret-token" not in report
    assert "[redacted: possible secret]" in report
    assert "### Manager Decisions" in report
    assert "Accepted findings: F1" in report
    assert "F1 affects test correctness" in report
    assert "https://github.com/org/repo/pull/99" in report
    assert "### Final State" in report


def test_write_task_report_via_module(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-write"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(
        json.dumps({"run_id": "run-write", "task": "Write report", "status": "no_changes"}),
        encoding="utf-8",
    )

    report_path = write_task_report(tmp_path, run_dir)

    assert report_path.is_file()
    assert "## HOCA Task Report" in report_path.read_text(encoding="utf-8")
