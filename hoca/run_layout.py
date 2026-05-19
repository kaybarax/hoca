"""Standard per-run directory layout under `.hoca-runtime/runs/<run_id>/`."""

from __future__ import annotations

from pathlib import Path

RUN_SUBDIRS: tuple[str, ...] = ("attempts", "reviews", "decisions", "validation", "logs")

TASK_SPEC_FILENAME = "task-spec.json"
SANDBOX_POLICY_FILENAME = "sandbox-policy.json"
STATUS_FILENAME = "status.json"
FINAL_STATE_FILENAME = "final-state.json"


def worker_attempt_path(run_dir: Path, round_number: int) -> Path:
    return run_dir / "attempts" / f"worker-attempt-{round_number}.json"


def review_report_path(run_dir: Path, round_number: int) -> Path:
    return run_dir / "reviews" / f"review-report-{round_number}.json"


def manager_decision_path(run_dir: Path, round_number: int) -> Path:
    return run_dir / "decisions" / f"manager-decision-{round_number}.json"


def validation_report_path(run_dir: Path, round_number: int) -> Path:
    return run_dir / "validation" / f"validation-report-{round_number}.json"


def task_spec_path(run_dir: Path) -> Path:
    return run_dir / TASK_SPEC_FILENAME


def sandbox_policy_path(run_dir: Path) -> Path:
    return run_dir / SANDBOX_POLICY_FILENAME


def status_path(run_dir: Path) -> Path:
    return run_dir / STATUS_FILENAME


def final_state_path(run_dir: Path) -> Path:
    return run_dir / FINAL_STATE_FILENAME


def ensure_run_layout(run_dir: Path) -> None:
    """Create the standard run directory subdirectories."""
    run_dir.mkdir(parents=True, exist_ok=True)
    for subdir in RUN_SUBDIRS:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)
