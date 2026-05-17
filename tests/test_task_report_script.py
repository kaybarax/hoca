from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
