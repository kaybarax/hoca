"""Write structured run artifacts into the standard run directory layout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hoca.arbitration import arbitrate
from hoca.config import load_config
from hoca.contracts import (
    HocaAttemptReport,
    HocaReviewReport,
    HocaRoleModelSelection,
    HocaRunFinalState,
    HocaSandboxPolicy,
    HocaTaskSpec,
    HocaValidationReport,
)
from hoca.hard_blockers import ValidationStatus, validation_blocker_from_monitor_stop_reason
from hoca.run_layout import (
    ensure_run_layout,
    manager_decision_path,
    review_report_path,
    sandbox_policy_path,
    task_spec_path,
    validation_report_path,
    worker_attempt_path,
)
from hoca.run_state import (
    list_round_artifact_paths,
    read_json,
    read_optional_json,
    write_final_state,
    write_json_atomic,
)


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_monitor_result(run_dir: Path) -> dict[str, Any]:
    monitor_path = run_dir / "monitor-result.json"
    if not monitor_path.is_file():
        return {}
    try:
        loaded = read_json(monitor_path)
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _role_models_from_config() -> dict[str, str]:
    cfg = load_config()
    pool = cfg.model_pool
    fallback = pool.fallback_model or pool.worker_model or pool.manager_model or "default"
    return {
        "manager": pool.manager_model or fallback,
        "worker": pool.worker_model or fallback,
        "reviewer": pool.reviewer_model or fallback,
        "fallback": fallback,
    }


def build_initial_task_spec(
    *,
    run_id: str,
    repo_root: str,
    base_branch: str,
    task_branch: str,
    raw_request: str,
    issue_id: str | None,
    max_total_rounds: int,
    sandbox: HocaSandboxPolicy,
) -> HocaTaskSpec:
    roles = _role_models_from_config()
    return HocaTaskSpec(
        run_id=run_id,
        repo_root=repo_root,
        base_branch=base_branch,
        task_branch=task_branch,
        issue_id=issue_id,
        raw_request=raw_request,
        goal=raw_request.strip(),
        non_goals=[],
        expected_areas=[],
        acceptance_criteria=["Task completes with passing validation and review"],
        test_commands=[],
        risk_level="low",
        requires_human_approval=True,
        max_total_rounds=max_total_rounds,
        models=HocaRoleModelSelection(
            manager=roles["manager"],
            worker=roles["worker"],
            reviewer=roles["reviewer"],
            fallback=roles["fallback"],
        ),
        sandbox=sandbox,
    )


def init_run_layout(
    run_dir: Path,
    *,
    run_id: str,
    repo_root: str,
    base_branch: str,
    task_branch: str,
    raw_request: str,
    issue_id: str | None = None,
    max_total_rounds: int | None = None,
    sandbox_enabled: bool | None = None,
    sandbox_network_mode: str | None = None,
) -> None:
    ensure_run_layout(run_dir)
    cfg = load_config()
    sandbox = HocaSandboxPolicy(
        enabled=cfg.use_sandbox if sandbox_enabled is None else sandbox_enabled,
        network_mode=sandbox_network_mode or "offline",
    )
    spec = build_initial_task_spec(
        run_id=run_id,
        repo_root=repo_root,
        base_branch=base_branch,
        task_branch=task_branch,
        raw_request=raw_request,
        issue_id=issue_id,
        max_total_rounds=max_total_rounds or cfg.max_total_rounds,
        sandbox=sandbox,
    )
    write_json_atomic(task_spec_path(run_dir), spec.to_dict())
    write_json_atomic(sandbox_policy_path(run_dir), sandbox.to_dict())


def record_worker_attempt(
    run_dir: Path,
    *,
    round_number: int,
    status: str,
    summary: list[str] | None = None,
) -> Path:
    ensure_run_layout(run_dir)
    run_id = run_dir.name
    changed_files = _read_lines(run_dir / "changed-files-after-openhands.txt")
    if not changed_files:
        changed_files = _read_lines(run_dir / "changed-files.txt")

    monitor = _load_monitor_result(run_dir)
    output_name = "openhands-output.jsonl"
    if not (run_dir / output_name).is_file() and (run_dir / "openhands-output.log").is_file():
        output_name = "openhands-output.log"

    artifact_paths = {
        "openhands_output": str(run_dir / output_name),
        "monitor_result": str(run_dir / "monitor-result.json"),
    }
    blocked_reason = None
    if status != "completed":
        if monitor.get("stop_reason"):
            blocked_reason = str(monitor["stop_reason"])
        elif (run_dir / "openhands-error.txt").is_file():
            blocked_reason = (run_dir / "openhands-error.txt").read_text(encoding="utf-8").strip()

    report = HocaAttemptReport(
        run_id=run_id,
        round=round_number,
        role="worker",
        status=status,
        changed_files=changed_files,
        summary=summary or [f"Worker attempt {round_number} recorded with status {status}."],
        commands_run=["run-openhands-task.sh"],
        tests_run=[],
        known_risks=[],
        blocked_reason=blocked_reason,
        artifact_paths=artifact_paths,
    )
    path = worker_attempt_path(run_dir, round_number)
    write_json_atomic(path, report.to_dict())
    return path


def build_validation_status_from_run_dir(run_dir: Path) -> ValidationStatus:
    monitor = _load_monitor_result(run_dir)
    stop_reason = monitor.get("stop_reason")
    if isinstance(stop_reason, str) and not stop_reason.strip():
        stop_reason = None

    tests_passed = True
    exit_code_path = run_dir / "tests-exit-code.txt"
    if exit_code_path.is_file():
        try:
            tests_passed = int(exit_code_path.read_text(encoding="utf-8").strip()) == 0
        except ValueError:
            tests_passed = False

    hard_blockers: list[str] = []
    if (run_dir / "secret-detected.txt").is_file():
        hard_blockers.append("secret_file_change")

    monitor_clean = True
    if stop_reason and stop_reason != "completed":
        monitor_clean = False
        monitor_blocker = validation_blocker_from_monitor_stop_reason(str(stop_reason))
        if monitor_blocker:
            hard_blockers.append(monitor_blocker)

    return ValidationStatus(
        tests_passed=tests_passed,
        hard_blockers=tuple(sorted(set(hard_blockers))),
        secret_scan_clean="secret_file_change" not in hard_blockers,
        monitor_clean=monitor_clean,
        monitor_stop_reason=str(stop_reason) if stop_reason else None,
    )


def record_validation_report(run_dir: Path, *, round_number: int) -> Path:
    ensure_run_layout(run_dir)
    validation = build_validation_status_from_run_dir(run_dir)
    failure_type = ""
    summary_path = run_dir / "tests-summary.md"
    if summary_path.is_file():
        for line in summary_path.read_text(encoding="utf-8").splitlines():
            if "Failure type" in line:
                failure_type = line.split(":", 1)[-1].strip().strip("*")
                break

    report = HocaValidationReport(
        run_id=run_dir.name,
        round=round_number,
        tests_passed=validation.tests_passed,
        test_failure_type=failure_type or None,
        git_status=_read_lines(run_dir / "git-status.txt"),
        changed_files=_read_lines(run_dir / "changed-files.txt"),
        secret_scan_clean=validation.secret_scan_clean,
        monitor_clean=validation.monitor_clean,
        monitor_stop_reason=validation.monitor_stop_reason,
        hard_blockers=list(validation.hard_blockers),
        scope_risk=False,
        staging_risk=False,
        artifact_paths={
            "tests_summary": str(run_dir / "tests-summary.md"),
            "tests_output": str(run_dir / "tests-output.log"),
            "monitor_result": str(run_dir / "monitor-result.json"),
        },
    )
    path = validation_report_path(run_dir, round_number)
    write_json_atomic(path, report.to_dict())
    return path


def record_manager_decision(run_dir: Path, *, round_number: int) -> Path | None:
    ensure_run_layout(run_dir)
    review_path = review_report_path(run_dir, round_number)
    if not review_path.is_file():
        return None

    review = HocaReviewReport.from_json(review_path.read_text(encoding="utf-8"))
    validation = build_validation_status_from_run_dir(run_dir)
    cfg = load_config()
    decision = arbitrate(
        review=review,
        validation=validation,
        max_total_rounds=cfg.max_total_rounds,
    )
    path = manager_decision_path(run_dir, round_number)
    write_json_atomic(path, decision.to_dict())
    return path


def _map_status_to_final(status: str) -> str:
    mapping = {
        "pr_created": "pr_opened",
        "committed": "completed",
        "staged": "completed",
        "no_changes": "completed",
        "blocked": "blocked",
        "failed": "failed",
        "needs_human_staging": "draft_pr_opened",
    }
    return mapping.get(status, "completed")


def record_final_state(run_dir: Path) -> Path:
    ensure_run_layout(run_dir)
    status_data = read_optional_json(run_dir / "status.json") or {}
    status = str(status_data.get("status", "completed"))
    pr_url = None
    pr_url_path = run_dir / "pr-url.txt"
    if pr_url_path.is_file():
        pr_url = pr_url_path.read_text(encoding="utf-8").strip() or None

    summary = [f"Run finished with status {status}."]
    reason = status_data.get("reason")
    if reason:
        summary.append(f"Reason: {reason}")

    state = HocaRunFinalState(
        run_id=run_dir.name,
        status=_map_status_to_final(status),
        summary=summary,
        changed_files=_read_lines(run_dir / "changed-files.txt"),
        tests_run=_read_lines(run_dir / "tests-summary.md")[:5],
        attempt_reports=list_round_artifact_paths(run_dir, "attempts", "worker-attempt-"),
        review_reports=list_round_artifact_paths(run_dir, "reviews", "review-report-"),
        manager_decisions=list_round_artifact_paths(run_dir, "decisions", "manager-decision-"),
        pr_url=pr_url,
        completed_at=status_data.get("ended_at") or status_data.get("started_at"),
        blocked_reason=str(reason) if status in ("blocked", "failed") and reason else None,
    )
    return write_final_state(run_dir, state.to_dict())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record HOCA structured run artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create run layout and initial artifacts.")
    init_parser.add_argument("run_dir")
    init_parser.add_argument("--run-id", required=True)
    init_parser.add_argument("--repo-root", required=True)
    init_parser.add_argument("--base-branch", required=True)
    init_parser.add_argument("--task-branch", required=True)
    init_parser.add_argument("--task", required=True)
    init_parser.add_argument("--issue-id")
    init_parser.add_argument("--max-total-rounds", type=int)

    worker_parser = subparsers.add_parser("record-worker", help="Write worker-attempt report.")
    worker_parser.add_argument("run_dir")
    worker_parser.add_argument("--round", type=int, required=True)
    worker_parser.add_argument("--status", default="completed")

    validation_parser = subparsers.add_parser("record-validation", help="Write validation report.")
    validation_parser.add_argument("run_dir")
    validation_parser.add_argument("--round", type=int, required=True)

    decision_parser = subparsers.add_parser("record-decision", help="Write manager decision.")
    decision_parser.add_argument("run_dir")
    decision_parser.add_argument("--round", type=int, required=True)

    final_parser = subparsers.add_parser("record-final", help="Write final-state.json.")
    final_parser.add_argument("run_dir")

    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).resolve()

    try:
        if args.command == "init":
            init_run_layout(
                run_dir,
                run_id=args.run_id,
                repo_root=args.repo_root,
                base_branch=args.base_branch,
                task_branch=args.task_branch,
                raw_request=args.task,
                issue_id=args.issue_id,
                max_total_rounds=args.max_total_rounds,
            )
        elif args.command == "record-worker":
            path = record_worker_attempt(run_dir, round_number=args.round, status=args.status)
            print(path)
        elif args.command == "record-validation":
            path = record_validation_report(run_dir, round_number=args.round)
            print(path)
        elif args.command == "record-decision":
            path = record_manager_decision(run_dir, round_number=args.round)
            if path is None:
                print("No structured review report found; skipped manager decision.", file=sys.stderr)
                return 1
            print(path)
        elif args.command == "record-final":
            path = record_final_state(run_dir)
            print(path)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
