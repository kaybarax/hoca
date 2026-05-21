from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_safe_staging_scripts import init_repo, staged_files


HOCA_ROOT = Path(__file__).resolve().parents[1]
GENERATE_TASK_SPEC = HOCA_ROOT / "scripts" / "generate-task-spec.sh"
SAFE_STAGE = HOCA_ROOT / "scripts" / "safe-stage-after-review.sh"
GENERATE_TASK_REPORT = HOCA_ROOT / "scripts" / "generate-task-report.sh"


def run_script(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HOCA_ROOT)
    env["HOCA_PYTHON"] = sys.executable
    return subprocess.run(
        [*args],
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_offline_integration_generates_spec_stages_reviewed_change_and_reports(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "greeting.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", "docs/greeting.md"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "commit", "-m", "add greeting doc"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    run_id = "run-offline-integration"
    run_dir = repo / ".hoca-runtime" / "runs" / run_id
    task = "Update docs/greeting.md to include a friendly HOCA greeting"

    spec_result = run_script(
        str(GENERATE_TASK_SPEC),
        str(repo),
        task,
        str(run_dir),
        "--run-id",
        run_id,
        "--base-branch",
        "main",
        "--task-branch",
        "feat/offline-integration",
        cwd=HOCA_ROOT,
    )

    assert spec_result.returncode == 0, spec_result.stderr
    task_spec = json.loads((run_dir / "task-spec.json").read_text(encoding="utf-8"))
    assert task_spec["run_id"] == run_id
    assert task_spec["raw_request"] == task
    assert task_spec["expected_areas"] == ["docs/greeting.md"]
    assert task_spec["sandbox"]["network_mode"] == "offline"

    (repo / "docs" / "greeting.md").write_text(
        "hello\n\nThis greeting was updated by the offline HOCA integration fixture.\n",
        encoding="utf-8",
    )
    (run_dir / "changed-files.txt").write_text("docs/greeting.md\n", encoding="utf-8")
    (run_dir / "intended-files.txt").write_text("docs/greeting.md\n", encoding="utf-8")
    (run_dir / "intended-files-source.txt").write_text("manager\n", encoding="utf-8")
    (run_dir / "openhands-output.log").write_text(
        "Simulated worker updated docs/greeting.md.\n",
        encoding="utf-8",
    )
    (run_dir / "monitor-result.json").write_text('{"clean": true}\n', encoding="utf-8")
    (run_dir / "openhands-review.txt").write_text(
        "Structured review artifact is authoritative.\nLGTM\n",
        encoding="utf-8",
    )
    (run_dir / "tests-summary.md").write_text(
        "# Test Summary\n\n- **Status**: passed\n- **Command**: `offline fixture validation`\n",
        encoding="utf-8",
    )
    write_json(
        run_dir / "status.json",
        {
            "run_id": run_id,
            "task": task,
            "status": "staged",
            "started_at": "2026-05-21T00:00:00Z",
            "auto_merge": "false",
            "merge_performed": False,
        },
    )
    write_json(
        run_dir / "attempts" / "worker-attempt-1.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "round": 1,
            "role": "worker",
            "status": "completed",
            "changed_files": ["docs/greeting.md"],
            "summary": ["Updated the greeting fixture documentation."],
            "commands_run": ["offline file edit simulation"],
            "tests_run": ["offline fixture validation"],
            "known_risks": [],
            "blocked_reason": None,
            "artifact_paths": {
                "openhands_output": "openhands-output.log",
                "monitor_result": "monitor-result.json",
            },
        },
    )
    write_json(
        run_dir / "reviews" / "review-report-1.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "round": 1,
            "role": "reviewer",
            "verdict": "LGTM",
            "findings": [],
            "pr_notes": {
                "summary": ["Offline reviewer simulation approved the scoped doc change."],
                "known_followups": [],
            },
        },
    )
    write_json(
        run_dir / "decisions" / "manager-decision-1.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "round": 1,
            "decision": "proceed_to_pr",
            "accepted_findings": [],
            "rejected_findings": [],
            "downgraded_to_pr_notes": [],
            "reasoning": [
                "Worker changed only the expected fixture file.",
                "Reviewer returned LGTM with no findings.",
            ],
            "next_worker_brief": None,
            "human_attention_required": False,
        },
    )
    write_json(
        run_dir / "validation" / "validation-report-1.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "round": 1,
            "tests_passed": True,
            "test_failure_type": None,
            "git_status": [" M docs/greeting.md"],
            "changed_files": ["docs/greeting.md"],
            "secret_scan_clean": True,
            "monitor_clean": True,
            "monitor_stop_reason": None,
            "hard_blockers": [],
            "scope_risk": False,
            "staging_risk": False,
            "artifact_paths": {},
        },
    )

    stage_result = run_script(
        str(SAFE_STAGE),
        str(repo),
        task,
        str(run_dir),
        str(run_dir / "intended-files.txt"),
        cwd=HOCA_ROOT,
    )

    assert stage_result.returncode == 0, stage_result.stderr
    assert staged_files(repo) == ["docs/greeting.md"]
    assert (run_dir / "staged-files.txt").read_text(encoding="utf-8") == "docs/greeting.md\n"

    write_json(
        run_dir / "final-state.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "status": "completed",
            "reason": "offline_integration_completed",
            "summary": [
                "Generated a task spec.",
                "Simulated worker, reviewer, and manager artifacts.",
                "Safely staged reviewed fixture changes.",
            ],
            "changed_files": ["docs/greeting.md"],
            "tests_run": ["offline fixture validation"],
            "attempt_reports": ["attempts/worker-attempt-1.json"],
            "review_reports": ["reviews/review-report-1.json"],
            "manager_decisions": ["decisions/manager-decision-1.json"],
            "pr_url": None,
            "human_attention_required": False,
            "unresolved_findings": [],
            "completed_at": "2026-05-21T00:01:00Z",
            "blocked_reason": None,
        },
    )

    report_result = run_script(
        str(GENERATE_TASK_REPORT),
        str(repo),
        str(run_dir),
        cwd=HOCA_ROOT,
    )

    assert report_result.returncode == 0, report_result.stderr
    report = (run_dir / "task-report.md").read_text(encoding="utf-8")
    assert "## HOCA Task Report" in report
    assert "### Worker Attempts" in report
    assert "Updated the greeting fixture documentation." in report
    assert "### Manager Decisions" in report
    assert "- Decision: proceed_to_pr" in report
    assert "### Code Review\n- Status: LGTM" in report
    assert "Staged files:\n- docs/greeting.md" in report
    assert "### Final State" in report
    assert "- Status: completed" in report
