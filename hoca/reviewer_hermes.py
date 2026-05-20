"""Run a reviewer pass via Hermes profile or legacy OpenHands wrapper."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hoca.config import load_config
from hoca.contracts import HocaReviewFinding, HocaReviewReport, HocaTaskSpec
from hoca.paths import repo_root
from hoca.profiles import PROFILE_REVIEWER, hermes_installed, profile_exists
from hoca.review_gate import ReviewGateError, evaluate_review_gate
from hoca.run_layout import ensure_run_layout, review_report_path, worker_attempt_path
from hoca.subprocess_utils import CommandResult, run_command
from hoca.worker_hermes import load_task_spec

_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[:=]\s*\S+"
)

DEFAULT_HERMES_TIMEOUT_SECONDS = 1800
DEFAULT_HERMES_MAX_TURNS = 20


@dataclass(frozen=True)
class ReviewerRunResult:
    mode: str
    exit_code: int
    review_report_path: Path | None
    hermes_stdout_path: Path | None = None
    hermes_stderr_path: Path | None = None


@dataclass(frozen=True)
class ReviewerInputs:
    changed_files_path: Path
    diff_path: Path
    test_summary_path: Path
    worker_report_path: Path | None


def _redact_secret_like_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if _SECRET_VALUE_PATTERN.search(line):
            lines.append("[redacted: possible secret]")
        else:
            lines.append(line)
    return "\n".join(lines)


def _resolve_timeout_seconds() -> int:
    raw = os.environ.get("HOCA_HERMES_TIMEOUT", str(DEFAULT_HERMES_TIMEOUT_SECONDS))
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise ValueError(f"HOCA_HERMES_TIMEOUT must be an integer, got: {raw!r}") from exc
    if timeout <= 0:
        raise ValueError("HOCA_HERMES_TIMEOUT must be greater than 0")
    return timeout


def _resolve_max_turns() -> int:
    raw = os.environ.get("HOCA_HERMES_MAX_TURNS", str(DEFAULT_HERMES_MAX_TURNS))
    try:
        max_turns = int(raw)
    except ValueError as exc:
        raise ValueError(f"HOCA_HERMES_MAX_TURNS must be an integer, got: {raw!r}") from exc
    if max_turns <= 0:
        raise ValueError("HOCA_HERMES_MAX_TURNS must be greater than 0")
    return max_turns


def verify_profile_prerequisites(*, hermes_home: Path | None = None) -> None:
    if not hermes_installed():
        raise RuntimeError(
            "hermes command not found. Install Hermes or disable HOCA_USE_HERMES_PROFILES."
        )
    if not profile_exists(PROFILE_REVIEWER, hermes_home=hermes_home):
        raise RuntimeError(
            f"Hermes profile {PROFILE_REVIEWER!r} is not installed. "
            "Run scripts/setup-hermes-profiles.sh before enabling profile mode."
        )


def _git_output(project_path: Path, *args: str) -> str:
    result = run_command(["git", *args], cwd=project_path)
    if result.returncode != 0:
        return ""
    return result.stdout


def _changed_files(project_path: Path) -> list[str]:
    paths: set[str] = set()
    for command in (
        ("diff", "--name-only", "--diff-filter=ACMRTUXB"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        for line in _git_output(project_path, *command).splitlines():
            changed_path = line.strip()
            if not changed_path or changed_path == ".hoca-runtime":
                continue
            if changed_path.startswith(".hoca-runtime/"):
                continue
            if (project_path / changed_path).exists():
                paths.add(changed_path)
    return sorted(paths)


def _latest_worker_report(run_dir: Path, round_number: int) -> Path | None:
    exact_path = worker_attempt_path(run_dir, round_number)
    if exact_path.is_file():
        return exact_path
    reports = sorted((run_dir / "attempts").glob("worker-attempt-*.json"))
    return reports[-1] if reports else None


def prepare_reviewer_inputs(
    *, project_path: Path, run_dir: Path, round_number: int
) -> ReviewerInputs:
    review_dir = run_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    changed_files_path = review_dir / "changed-files.txt"
    diff_path = review_dir / "git-diff.patch"
    changed_files_path.write_text(
        "".join(f"{path}\n" for path in _changed_files(project_path)),
        encoding="utf-8",
    )
    diff_path.write_text(_git_output(project_path, "diff"), encoding="utf-8")
    return ReviewerInputs(
        changed_files_path=changed_files_path,
        diff_path=diff_path,
        test_summary_path=run_dir / "tests-summary.md",
        worker_report_path=_latest_worker_report(run_dir, round_number),
    )


def build_reviewer_hermes_prompt(
    *,
    spec: HocaTaskSpec,
    project_path: Path,
    run_dir: Path,
    round_number: int,
    task_spec_path: Path,
    inputs: ReviewerInputs,
) -> str:
    hoca_root = repo_root()
    report_path = review_report_path(run_dir, round_number)
    worker_report = str(inputs.worker_report_path) if inputs.worker_report_path else "(missing)"
    prompt = (
        "Execute one bounded HOCA reviewer pass using the hoca-reviewer-qa skill.\n\n"
        "Assignment parameters:\n"
        f"- project_path: {project_path.resolve()}\n"
        f"- run_dir: {run_dir.resolve()}\n"
        f"- round: {round_number}\n"
        f"- task_spec_path: {task_spec_path.resolve()}\n"
        f"- hoca_root: {hoca_root}\n"
        f"- changed_files_path: {inputs.changed_files_path.resolve()}\n"
        f"- diff_path: {inputs.diff_path.resolve()}\n"
        f"- test_summary_path: {inputs.test_summary_path.resolve()}\n"
        f"- worker_report_path: {worker_report}\n"
        f"- required_review_report_path: {report_path.resolve()}\n\n"
        "Required steps:\n"
        "1. Read the task spec, changed-file list, diff, test summary, and worker report.\n"
        "2. Review correctness, security, tests, scope, maintainability, and unrelated edits.\n"
        "3. Do not implement changes, stage files, commit, push, merge, or open pull requests.\n"
        "4. Write exactly one structured HocaReviewReport JSON file at required_review_report_path.\n"
        "5. Use verdict LGTM only when there are no blocking findings. Use fix_required or blocked otherwise.\n\n"
        "Task spec summary (read the JSON file for full fields):\n"
        f"- goal: {spec.goal.strip()}\n"
        f"- acceptance_criteria: {', '.join(spec.acceptance_criteria) or '(none)'}\n"
        f"- expected_areas: {', '.join(spec.expected_areas) or '(none)'}\n"
        f"- non_goals: {', '.join(spec.non_goals) or '(none)'}\n"
    )
    return _redact_secret_like_lines(prompt)


def _invoke_hermes_reviewer(
    *,
    prompt: str,
    run_dir: Path,
    timeout_seconds: int,
    max_turns: int,
) -> CommandResult:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "reviewer-hermes-stdout.txt"
    stderr_path = logs_dir / "reviewer-hermes-stderr.txt"
    command = [
        "hermes",
        "-p",
        PROFILE_REVIEWER,
        "-z",
        prompt,
        "--accept-hooks",
        "-s",
        "hoca-reviewer-qa",
        "--max-turns",
        str(max_turns),
    ]
    env = os.environ.copy()
    env.setdefault("HERMES_ACCEPT_HOOKS", "1")

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            cwd=run_dir,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nHermes reviewer timed out after {timeout_seconds}s."
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return CommandResult(tuple(command), 124, stdout, stderr)

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return CommandResult(
        tuple(command), completed.returncode, completed.stdout, completed.stderr
    )


def _write_blocked_report(
    *,
    run_dir: Path,
    round_number: int,
    reason: str,
) -> Path:
    report = HocaReviewReport(
        run_id=run_dir.name,
        round=round_number,
        role="reviewer",
        verdict="blocked",
        findings=[
            HocaReviewFinding(
                id=f"reviewer-runner-{round_number}",
                severity="high",
                category="tooling",
                file=None,
                summary="Reviewer profile output could not be approved.",
                required_fix=reason,
            )
        ],
        pr_notes={
            "summary": ["Reviewer profile output was missing, malformed, or failed."],
            "known_followups": [reason],
        },
    )
    path = review_report_path(run_dir, round_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_json(), encoding="utf-8")
    return path


def _evaluate_profile_report(
    *, run_dir: Path, round_number: int, process_exit_code: int
) -> tuple[Path, int]:
    report_path = review_report_path(run_dir, round_number)
    if not report_path.exists():
        path = _write_blocked_report(
            run_dir=run_dir,
            round_number=round_number,
            reason="Hermes reviewer did not write a structured HocaReviewReport.",
        )
        return path, 4
    try:
        result = evaluate_review_gate(
            run_dir,
            review_text_path=run_dir / "logs" / "reviewer-hermes-stdout.txt",
            run_id=run_dir.name,
            round_number=round_number,
            structured_report_path=report_path,
        )
    except ReviewGateError as exc:
        malformed_copy = run_dir / "logs" / f"malformed-review-report-{round_number}.json"
        if report_path.exists():
            malformed_copy.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
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
            reason=f"Hermes reviewer exited with code {process_exit_code}.",
        )
        return path, 4
    if result.report.verdict == "LGTM":
        return result.report_path, 0
    if result.report.verdict == "fix_required":
        return result.report_path, 2
    return result.report_path, 4


def _run_legacy_review_wrapper(
    *,
    project_path: Path,
    task: str,
    run_dir: Path,
    round_number: int,
) -> CommandResult:
    script = repo_root() / "scripts" / "review-with-openhands.sh"
    env = os.environ.copy()
    env["HOCA_REVIEW_ROUND"] = str(round_number)
    completed = subprocess.run(
        [str(script), str(project_path), task, str(run_dir)],
        cwd=project_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return CommandResult(
        (str(script), str(project_path), task, str(run_dir)),
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )


def run_reviewer_hermes(
    *,
    project_path: Path,
    task_spec_path: Path,
    run_dir: Path,
    round_number: int,
    use_hermes_profiles: bool | None = None,
    hermes_home: Path | None = None,
) -> ReviewerRunResult:
    if round_number < 1:
        raise ValueError("round must be greater than or equal to 1")

    project_path = project_path.resolve()
    run_dir = run_dir.resolve()
    task_spec_path = task_spec_path.resolve()
    ensure_run_layout(run_dir)
    spec = load_task_spec(task_spec_path)
    inputs = prepare_reviewer_inputs(
        project_path=project_path, run_dir=run_dir, round_number=round_number
    )
    cfg = load_config()
    profile_mode = cfg.use_hermes_profiles if use_hermes_profiles is None else use_hermes_profiles

    if profile_mode:
        verify_profile_prerequisites(hermes_home=hermes_home)
        prompt = build_reviewer_hermes_prompt(
            spec=spec,
            project_path=project_path,
            run_dir=run_dir,
            round_number=round_number,
            task_spec_path=task_spec_path,
            inputs=inputs,
        )
        (run_dir / f"reviewer-hermes-prompt-round-{round_number}.txt").write_text(
            prompt + "\n", encoding="utf-8"
        )
        result = _invoke_hermes_reviewer(
            prompt=prompt,
            run_dir=run_dir,
            timeout_seconds=_resolve_timeout_seconds(),
            max_turns=_resolve_max_turns(),
        )
        report_path, exit_code = _evaluate_profile_report(
            run_dir=run_dir,
            round_number=round_number,
            process_exit_code=result.returncode,
        )
        return ReviewerRunResult(
            mode="profile",
            exit_code=exit_code,
            review_report_path=report_path,
            hermes_stdout_path=run_dir / "logs" / "reviewer-hermes-stdout.txt",
            hermes_stderr_path=run_dir / "logs" / "reviewer-hermes-stderr.txt",
        )

    result = _run_legacy_review_wrapper(
        project_path=project_path,
        task=spec.goal,
        run_dir=run_dir,
        round_number=round_number,
    )
    return ReviewerRunResult(
        mode="legacy",
        exit_code=result.returncode,
        review_report_path=review_report_path(run_dir, round_number),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a HOCA reviewer pass via Hermes profile or legacy OpenHands wrapper."
    )
    parser.add_argument("project_path", help="Path to the target Git repository")
    parser.add_argument("task_spec_path", help="Path to task-spec.json")
    parser.add_argument("run_dir", help="HOCA run directory (.hoca-runtime/runs/<run_id>)")
    parser.add_argument("round", type=int, help="Review round number (>= 1)")
    args = parser.parse_args(argv)

    try:
        result = run_reviewer_hermes(
            project_path=Path(args.project_path),
            task_spec_path=Path(args.task_spec_path),
            run_dir=Path(args.run_dir),
            round_number=args.round,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if result.review_report_path is not None:
        print(result.review_report_path)
    if result.exit_code != 0:
        return result.exit_code if result.exit_code > 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
