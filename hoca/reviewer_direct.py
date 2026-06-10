"""Direct reviewer-mode execution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hoca.review_gate import ReviewGateError, evaluate_review_gate
from hoca.run_layout import ensure_run_layout, review_report_path
from hoca.subprocess_utils import CommandResult
from hoca.reviewer_hermes import (
    ReviewerRunResult,
    _normalize_profile_review_report,
    _write_blocked_report,
)


def _invoke_openhands_review_direct(
    *,
    project_path: Path,
    task: str,
    run_dir: Path,
    round_number: int,
) -> CommandResult:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "reviewer-direct-stdout.txt"
    stderr_path = logs_dir / "reviewer-direct-stderr.txt"
    hoca_root = Path(__file__).resolve().parents[1]
    command = [
        str(hoca_root / "scripts" / "review-with-openhands.sh"),
        str(project_path),
        task,
        str(run_dir),
    ]
    env = os.environ.copy()
    env["HOCA_AGENT_ROLE"] = "reviewer"
    env["HOCA_LOCK_ROLE_MODEL"] = "true"
    env["HOCA_REVIEW_ROUND"] = str(round_number)
    env["HOCA_REVIEW_REPORT_PATH"] = str(review_report_path(run_dir, round_number))
    env.setdefault("HOCA_PYTHON", sys.executable)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=run_dir,
        env=env,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return CommandResult(tuple(command), completed.returncode, completed.stdout, completed.stderr)


def _evaluate_direct_report(
    *, run_dir: Path, round_number: int, process_exit_code: int
) -> tuple[Path, int]:
    report_path = review_report_path(run_dir, round_number)
    if not report_path.exists():
        path = _write_blocked_report(
            run_dir=run_dir,
            round_number=round_number,
            reason="Direct reviewer did not write a structured HocaReviewReport.",
        )
        return path, 4
    _normalize_profile_review_report(report_path)
    try:
        result = evaluate_review_gate(
            run_dir,
            review_text_path=run_dir / "openhands-review.txt",
            run_id=run_dir.name,
            round_number=round_number,
            structured_report_path=report_path,
        )
    except ReviewGateError as exc:
        path = _write_blocked_report(
            run_dir=run_dir,
            round_number=round_number,
            reason=str(exc),
        )
        return path, 4
    if process_exit_code != 0 and result.report.verdict == "LGTM":
        path = _write_blocked_report(
            run_dir=run_dir,
            round_number=round_number,
            reason=f"Direct reviewer exited with code {process_exit_code}.",
        )
        return path, 4
    if result.report.verdict == "LGTM":
        return result.report_path, 0
    if result.report.verdict == "fix_required":
        return result.report_path, 2
    return result.report_path, 4


def run_reviewer_direct(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
) -> ReviewerRunResult:
    if round_number < 1:
        raise ValueError("round must be greater than or equal to 1")
    project_path = project_path.resolve()
    run_dir = run_dir.resolve()
    task_spec_path = task_spec_path.resolve()
    ensure_run_layout(run_dir)
    task = task_spec_path.read_text(encoding="utf-8")
    result = _invoke_openhands_review_direct(
        project_path=project_path,
        task=task,
        run_dir=run_dir,
        round_number=round_number,
    )
    report_path, exit_code = _evaluate_direct_report(
        run_dir=run_dir,
        round_number=round_number,
        process_exit_code=result.returncode,
    )
    return ReviewerRunResult(
        mode="direct",
        exit_code=exit_code,
        review_report_path=report_path,
        hermes_stdout_path=None,
        hermes_stderr_path=None,
    )
