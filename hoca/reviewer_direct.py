"""Direct reviewer-mode execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from hoca.review_gate import ReviewGateError, evaluate_review_gate
from hoca.review_report_parser import try_extract_structured_report
from hoca.run_layout import ensure_run_layout, review_report_path
from hoca.subprocess_utils import CommandResult
from hoca.reviewer_hermes import (
    ReviewerRunResult,
    _normalize_profile_review_report,
    _write_blocked_report,
)

DEFAULT_DIRECT_REVIEW_TIMEOUT_SECONDS = 600
DEFAULT_DIRECT_REVIEW_POST_EXIT_GRACE_SECONDS = 10


def _direct_review_timeout_seconds(env: dict[str, str]) -> int:
    raw = env.get("HOCA_OPENHANDS_TIMEOUT", str(DEFAULT_DIRECT_REVIEW_TIMEOUT_SECONDS))
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError(f"HOCA_OPENHANDS_TIMEOUT must be an integer, got: {raw!r}") from exc
    if timeout <= 0:
        raise ValueError("HOCA_OPENHANDS_TIMEOUT must be greater than 0")
    return timeout


def _structured_report_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(loaded, dict) and loaded.get("verdict") in {"LGTM", "fix_required", "blocked"}


def _recover_structured_report_from_log(log_path: Path, report_path: Path) -> bool:
    if not log_path.is_file():
        return False
    try:
        report = try_extract_structured_report(
            log_path.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return False
    if report is None:
        return False
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.to_json(), encoding="utf-8")
    return True


def _recover_structured_report_from_logs(log_paths: list[Path], report_path: Path) -> bool:
    for log_path in log_paths:
        if _recover_structured_report_from_log(log_path, report_path):
            return True
    return False


def _recover_report_from_alternate_paths(
    run_dir: Path, round_number: int, report_path: Path
) -> bool:
    # The review sandbox mounts the review dir at /hoca-run, so reviewers that
    # write through the mount land the report beside the review artifacts
    # instead of in reviews/.
    candidates = (
        run_dir / "review" / f"review-report-{round_number}.json",
        run_dir / f"review-report-{round_number}.json",
    )
    for candidate in candidates:
        if candidate == report_path or not _structured_report_ready(candidate):
            continue
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
        return True
    return False


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
    report_path = review_report_path(run_dir, round_number)
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
    env["HOCA_REVIEW_REPORT_PATH"] = str(report_path)
    env.setdefault("HOCA_PYTHON", sys.executable)
    timeout_seconds = _direct_review_timeout_seconds(env)
    deadline = time.monotonic() + timeout_seconds
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_file,
        stderr_path.open("w", encoding="utf-8") as stderr_file,
    ):
        process = subprocess.Popen(
            command,
            cwd=run_dir,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
        )
        while process.poll() is None:
            if (
                _structured_report_ready(report_path)
                or _recover_report_from_alternate_paths(run_dir, round_number, report_path)
                or _recover_structured_report_from_log(stdout_path, report_path)
                or _recover_structured_report_from_log(stderr_path, report_path)
            ):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                returncode = process.returncode if process.returncode is not None else 0
                return CommandResult(
                    tuple(command),
                    returncode,
                    stdout_path.read_text(encoding="utf-8", errors="replace"),
                    stderr_path.read_text(encoding="utf-8", errors="replace"),
                )
            if time.monotonic() >= deadline:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                timeout_message = (
                    f"\nDirect reviewer timed out after {timeout_seconds}s waiting for a "
                    "structured HocaReviewReport.\n"
                )
                return CommandResult(
                    tuple(command),
                    124,
                    stdout_path.read_text(encoding="utf-8", errors="replace"),
                    stderr_path.read_text(encoding="utf-8", errors="replace") + timeout_message,
                )
            time.sleep(0.5)
        post_exit_grace_deadline = time.monotonic() + DEFAULT_DIRECT_REVIEW_POST_EXIT_GRACE_SECONDS
        while time.monotonic() < post_exit_grace_deadline:
            if (
                _structured_report_ready(report_path)
                or _recover_report_from_alternate_paths(run_dir, round_number, report_path)
                or _recover_structured_report_from_log(stdout_path, report_path)
                or _recover_structured_report_from_log(stderr_path, report_path)
            ):
                break
            time.sleep(0.5)
        returncode = process.returncode if process.returncode is not None else 1
    return CommandResult(
        tuple(command),
        returncode,
        stdout_path.read_text(encoding="utf-8", errors="replace"),
        stderr_path.read_text(encoding="utf-8", errors="replace"),
    )


def _evaluate_direct_report(
    *, run_dir: Path, round_number: int, process_exit_code: int
) -> tuple[Path, int]:
    report_path = review_report_path(run_dir, round_number)
    stdout_path = run_dir / "logs" / "reviewer-direct-stdout.txt"
    stderr_path = run_dir / "logs" / "reviewer-direct-stderr.txt"
    candidate_logs = [
        stdout_path,
        stderr_path,
        run_dir / "openhands-review-stderr.log",
        run_dir / "openhands-review.txt",
        run_dir / "openhands-output.log",
        run_dir / "openhands-output.jsonl",
        run_dir / "openhands-stderr.log",
        run_dir / "review" / "openhands-output.log",
        run_dir / "review" / "openhands-output.jsonl",
        run_dir / "review" / "openhands-stderr.log",
    ]
    if not _structured_report_ready(report_path):
        _recover_report_from_alternate_paths(run_dir, round_number, report_path)
    if not _structured_report_ready(report_path):
        _recover_structured_report_from_logs(candidate_logs, report_path)
    if not _structured_report_ready(report_path):
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
