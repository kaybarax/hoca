"""Build human-readable HOCA task reports from run artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hoca.contracts import (
    HocaAttemptReport,
    HocaManagerDecision,
    HocaReviewFinding,
    HocaReviewReport,
    HocaRunFinalState,
    HocaTaskSpec,
    HocaValidationReport,
)
from hoca.pr_body import (
    format_hoca_review_notes_fragment,
    format_run_context_fragment,
    format_task_spec_fragment,
    is_draft_pr_run,
)
from hoca.review_gate import ReviewGateError, task_report_review_status, try_resolve_review_gate
from hoca.run_state import (
    current_round,
    current_run_round,
    read_optional_json,
    read_optional_report,
    summarize_run_for_pr_body,
)

_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[:=]\s*\S+"
)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._-]{10,}")
_ENV_SECRET_PATTERN = re.compile(
    r"(?i)(HOCA_MODEL_\d+_API_KEY|LLM_API_KEY|GITHUB_TOKEN|OPENAI_API_KEY)\s*=\s*\S+"
)


def _redact_secret_like_values(text: str) -> str:
    text = _SECRET_VALUE_PATTERN.sub("[redacted: possible secret]", text)
    text = _BEARER_TOKEN_PATTERN.sub("[redacted: possible secret]", text)
    text = _ENV_SECRET_PATTERN.sub(r"\1=[redacted: possible secret]", text)
    return text


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _read_lines(path: Path) -> list[str]:
    text = _read_text(path)
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _markdown_value(value: str | None) -> str:
    if value and value != "null":
        return value
    return "None"


def _bullet_list(items: list[str], *, empty: str = "- None recorded") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def _git_branch(project_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _load_attempt_report(run_dir: Path, round_number: int) -> HocaAttemptReport | None:
    payload = read_optional_report(run_dir, "worker_attempt", round_number=round_number)
    if not payload:
        return None
    try:
        return HocaAttemptReport.from_dict(payload)
    except ValueError:
        return None


def _load_review_report(run_dir: Path, round_number: int) -> HocaReviewReport | None:
    payload = read_optional_report(run_dir, "review_report", round_number=round_number)
    if not payload:
        return None
    try:
        return HocaReviewReport.from_dict(payload)
    except ValueError:
        return None


def _load_manager_decision(run_dir: Path, round_number: int) -> HocaManagerDecision | None:
    payload = read_optional_report(run_dir, "manager_decision", round_number=round_number)
    if not payload:
        return None
    try:
        return HocaManagerDecision.from_dict(payload)
    except ValueError:
        return None


def _load_validation_report(run_dir: Path, round_number: int) -> HocaValidationReport | None:
    payload = read_optional_report(run_dir, "validation_report", round_number=round_number)
    if not payload:
        return None
    try:
        return HocaValidationReport.from_dict(payload)
    except ValueError:
        return None


def _load_final_state(run_dir: Path) -> HocaRunFinalState | None:
    payload = read_optional_report(run_dir, "final_state")
    if not payload:
        return None
    try:
        return HocaRunFinalState.from_dict(payload)
    except ValueError:
        return None


def _load_task_spec(run_dir: Path) -> HocaTaskSpec | None:
    spec_path = run_dir / "task-spec.json"
    if not spec_path.is_file():
        return None
    try:
        return HocaTaskSpec.from_json(spec_path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _status_value(status: dict[str, Any], key: str, fallback: str = "") -> str:
    value = status.get(key, fallback)
    if value is None:
        return fallback
    return str(value)


def _merge_status_text(status: dict[str, Any]) -> str:
    merge_performed = status.get("merge_performed") is True
    auto_merge_queued = status.get("auto_merge_queued") is True
    auto_merge = str(status.get("auto_merge", "false")).lower() == "true"
    if merge_performed:
        return "merged"
    if auto_merge_queued:
        return "auto-merge enabled"
    if auto_merge:
        return "auto-merge requested"
    return "not merged"


def _status_summary_line(status: str) -> str:
    mapping = {
        "committed": "Task completed through commit creation.",
        "pr_created": "Task completed through pull request creation.",
        "staged": "Task completed through safe staging and is ready for commit.",
        "no_changes": "Task produced no repository changes.",
        "needs_human_staging": "Task completed through review and requires human staging.",
        "blocked": "Task stopped before completion.",
        "failed": "Task failed before completion.",
    }
    return mapping.get(status, f"Run status recorded as: {status}")


def _format_models_section(run_dir: Path) -> str | None:
    spec = _load_task_spec(run_dir)
    if spec is None:
        return None
    models = spec.models
    lines = [
        f"- Manager: {_markdown_value(models.manager)}",
        f"- Worker: {_markdown_value(models.worker)}",
        f"- Reviewer: {_markdown_value(models.reviewer)}",
        f"- Fallback: {_markdown_value(models.fallback)}",
    ]
    return "\n".join(lines)


def _format_worker_attempts_section(run_dir: Path) -> str | None:
    attempt_round = current_round(run_dir, prefix="worker-attempt-", subdir="attempts")
    if not attempt_round:
        return None

    lines: list[str] = []
    for round_number in range(1, attempt_round + 1):
        report = _load_attempt_report(run_dir, round_number)
        if report is None:
            lines.append(f"#### Round {round_number}")
            lines.append("- Attempt report present but could not be parsed.")
            lines.append("")
            continue

        lines.append(f"#### Round {round_number}")
        lines.append(f"- Status: {report.status}")
        if report.summary:
            lines.append("- Summary:")
            lines.extend(f"  - {_redact_secret_like_values(item)}" for item in report.summary[:8])
        if report.changed_files:
            lines.append(f"- Changed files: {', '.join(report.changed_files[:12])}")
        if report.blocked_reason:
            lines.append(
                f"- Blocked reason: {_redact_secret_like_values(report.blocked_reason)}"
            )
        if report.known_risks:
            lines.append("- Known risks:")
            lines.extend(f"  - {item}" for item in report.known_risks[:5])
        lines.append("")

    return "\n".join(lines).strip()


def _format_validation_section(run_dir: Path) -> str:
    validation_text = _read_text(run_dir / "tests-summary.md")
    if validation_text:
        return _redact_secret_like_values(validation_text)

    validation_round = current_round(run_dir, prefix="validation-report-", subdir="validation")
    if validation_round:
        lines: list[str] = []
        for round_number in range(1, validation_round + 1):
            report = _load_validation_report(run_dir, round_number)
            if report is None:
                continue
            lines.append(f"#### Round {round_number}")
            lines.append(f"- Tests passed: {report.tests_passed}")
            if report.test_failure_type:
                lines.append(f"- Failure type: {report.test_failure_type}")
            lines.append(f"- Secret scan clean: {report.secret_scan_clean}")
            lines.append(f"- Monitor clean: {report.monitor_clean}")
            if report.scope_risk:
                lines.append("- Scope risk: true")
            if report.staging_risk:
                lines.append("- Staging risk: true")
            if report.hard_blockers:
                lines.append(f"- Hard blockers: {', '.join(report.hard_blockers)}")
            lines.append("")
        if lines:
            return "\n".join(lines).strip()

    exit_code = _read_text(run_dir / "tests-exit-code.txt")
    if exit_code is not None:
        return f"- Test exit code: {exit_code}"
    return "- No validation summary recorded."


def _format_manager_decisions_section(run_dir: Path) -> str | None:
    decision_round = current_round(run_dir, prefix="manager-decision-", subdir="decisions")
    if not decision_round:
        return None

    lines: list[str] = []
    for round_number in range(1, decision_round + 1):
        decision = _load_manager_decision(run_dir, round_number)
        if decision is None:
            continue
        lines.append(f"#### Round {round_number}")
        lines.append(f"- Decision: {decision.decision}")
        if decision.reasoning:
            lines.append("- Reasoning:")
            lines.extend(
                f"  - {_redact_secret_like_values(item)}" for item in decision.reasoning[:8]
            )
        if decision.accepted_findings:
            lines.append(f"- Accepted findings: {', '.join(decision.accepted_findings)}")
        if decision.rejected_findings:
            lines.append(f"- Rejected findings: {', '.join(decision.rejected_findings)}")
        if decision.downgraded_to_pr_notes:
            lines.append(
                f"- Downgraded to PR notes: {', '.join(decision.downgraded_to_pr_notes)}"
            )
        if decision.next_worker_brief:
            lines.append("- Next worker brief:")
            for line in decision.next_worker_brief.strip().splitlines()[:6]:
                lines.append(f"  - {_redact_secret_like_values(line.strip())}")
        lines.append("")

    return "\n".join(lines).strip()


def _format_final_state_section(run_dir: Path) -> str | None:
    state = _load_final_state(run_dir)
    if state is None:
        return None

    lines = [
        f"- Status: {state.status}",
    ]
    if state.reason:
        lines.append(f"- Reason: {_redact_secret_like_values(state.reason)}")
    if state.blocked_reason:
        lines.append(f"- Blocked reason: {_redact_secret_like_values(state.blocked_reason)}")
    lines.append(
        f"- Human attention required: {'yes' if state.human_attention_required else 'no'}"
    )
    if state.pr_url:
        lines.append(f"- PR URL: {state.pr_url}")
    if state.unresolved_findings:
        lines.append(f"- Unresolved findings: {len(state.unresolved_findings)}")
        for finding in state.unresolved_findings[:8]:
            location = f" ({finding.file})" if finding.file else ""
            lines.append(f"  - {finding.id}{location}: {finding.summary}")
    if state.completed_at:
        lines.append(f"- Completed at: {state.completed_at}")
    if state.summary:
        lines.append("- Summary:")
        lines.extend(f"  - {_redact_secret_like_values(item)}" for item in state.summary[:8])
    return "\n".join(lines)


def _format_what_happened_section(
    run_dir: Path,
    *,
    status: str,
    reason: str,
    commit_hash: str | None,
) -> str:
    lines = [_status_summary_line(status)]
    final_state = _load_final_state(run_dir)
    if final_state is not None and final_state.summary:
        lines.append("")
        lines.extend(f"- {_redact_secret_like_values(item)}" for item in final_state.summary[:6])

    attempt_round = current_round(run_dir, prefix="worker-attempt-", subdir="attempts")
    review_round = current_round(run_dir, prefix="review-report-", subdir="reviews")
    decision_round = current_round(run_dir, prefix="manager-decision-", subdir="decisions")
    run_round = current_run_round(run_dir)

    if attempt_round or review_round or decision_round:
        lines.append("")
        lines.append(
            f"- Completed {attempt_round} worker attempt(s), "
            f"{review_round} review round(s), and "
            f"{decision_round} manager decision(s) "
            f"(max recorded round: {run_round or 0})."
        )

    if is_draft_pr_run(run_dir):
        lines.append("- Published as a draft PR with residual findings after the round cap.")

    if reason and reason != "null":
        lines.append(f"- Recorded reason: {_redact_secret_like_values(reason)}")

    if commit_hash:
        lines.append(f"- Commit created: `{commit_hash}`")

    return "\n".join(lines)


def _format_code_review_status(run_dir: Path) -> str:
    review_round = current_round(run_dir, prefix="review-report-", subdir="reviews") or 1
    legacy_review = run_dir / "openhands-review.txt"
    structured_review = run_dir / "reviews" / f"review-report-{review_round}.json"
    if not legacy_review.is_file() and not structured_review.is_file():
        return "Not run"

    try:
        result = try_resolve_review_gate(run_dir, round_number=review_round)
    except ReviewGateError:
        return "review gate error"

    if result is None:
        return "Not run"
    return task_report_review_status(result)


def _artifact_link_paths(run_dir: Path) -> list[str]:
    candidates = [
        run_dir / "raw-task.txt",
        run_dir / "task-spec.json",
        run_dir / "sandbox-policy.json",
        run_dir / "final-state.json",
        run_dir / "openhands-output.log",
        run_dir / "openhands-stderr.log",
        run_dir / "tests-output.log",
        run_dir / "tests-stderr.log",
        run_dir / "openhands-review.txt",
        run_dir / "openhands-review-stderr.log",
        run_dir / "git-status.txt",
        run_dir / "git-diff.patch",
        run_dir / "staged-diff.patch",
        run_dir / "gh-pr-create.log",
        run_dir / "gh-pr-merge.log",
        run_dir / "research-sources.txt",
        run_dir / "merge-policy.txt",
    ]
    for pattern in (
        "attempts/worker-attempt-*.json",
        "reviews/review-report-*.json",
        "decisions/manager-decision-*.json",
        "validation/validation-report-*.json",
    ):
        candidates.extend(sorted(run_dir.glob(pattern)))
    return [str(path) for path in candidates if path.is_file()]


def _task_section(run_dir: Path, *, task: str) -> str:
    raw_task = _read_text(run_dir / "raw-task.txt")
    if raw_task:
        body = "\n".join(raw_task.splitlines()[:80])
    else:
        body = _markdown_value(task)

    spec = _load_task_spec(run_dir)
    if spec is not None and spec.goal.strip():
        first_line = raw_task.splitlines()[0].strip() if raw_task else task.strip()
        if spec.goal.strip() != first_line:
            body = f"{body}\n\nRefined goal:\n{spec.goal.strip()}"

    structured = format_task_spec_fragment(run_dir, task_oneline=" ".join(task.split()))
    if "No structured `task-spec.json`" not in structured:
        body = structured
    return body


def build_task_report_markdown(project_path: Path, run_dir: Path) -> str:
    """Build the full human-readable task report for a HOCA run."""
    run_dir = run_dir.resolve()
    project_path = project_path.resolve()
    status = read_optional_json(run_dir / "status.json") or {}

    run_id = _status_value(status, "run_id", run_dir.name)
    task = _status_value(status, "task")
    issue_id = _status_value(status, "issue_id")
    final_status = _status_value(status, "status", "unknown")
    reason = _status_value(status, "reason")
    started_at = _status_value(status, "started_at")
    ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    branch = _git_branch(project_path)
    pr_url = _read_text(run_dir / "pr-url.txt") or _status_value(status, "pr_url")
    final_state = _load_final_state(run_dir)
    if not pr_url and final_state is not None and final_state.pr_url:
        pr_url = final_state.pr_url

    commit_hash = _read_text(run_dir / "commit-hash.txt")
    failed_command = _read_text(run_dir / "failed-command.txt")
    merge_status = _merge_status_text(status)

    changed_files = _read_lines(run_dir / "changed-files.txt")
    staged_files = _read_lines(run_dir / "staged-files.txt")

    fragments = summarize_run_for_pr_body(run_dir, task=task, issue_id=issue_id or None)

    sections: list[str] = [
        "## HOCA Task Report",
        "",
        "### Task",
        _task_section(run_dir, task=task),
        "",
        "### Run",
        f"- Run ID: {_markdown_value(run_id)}",
        f"- Issue ID: {_markdown_value(issue_id)}",
        f"- Start time: {_markdown_value(started_at)}",
        f"- End time: {ended_at}",
        f"- Final status: {_markdown_value(final_status)}",
    ]

    if reason and reason != "null":
        sections.append(f"- Blocked reason: {_redact_secret_like_values(reason)}")
    if failed_command:
        sections.append(f"- Failed command: `{failed_command}`")

    models_section = _format_models_section(run_dir)
    if models_section:
        sections.extend(["", "### Models", models_section])

    sections.extend(
        [
            "",
            "### Branch",
            _markdown_value(branch),
            "",
            "### Pull Request",
            _markdown_value(pr_url),
            "",
            "### Files Changed",
            _bullet_list(changed_files),
        ]
    )
    if staged_files:
        sections.extend(["", "Staged files:", _bullet_list(staged_files)])

    sections.extend(
        [
            "",
            "### What Happened",
            _format_what_happened_section(
                run_dir,
                status=final_status,
                reason=reason,
                commit_hash=commit_hash,
            ),
        ]
    )

    worker_attempts = _format_worker_attempts_section(run_dir)
    if worker_attempts:
        sections.extend(["", "### Worker Attempts", worker_attempts])

    sections.extend(["", "### Validation", _format_validation_section(run_dir)])

    sections.extend(
        [
            "",
            "### Code Review",
            f"- Status: {_format_code_review_status(run_dir)}",
        ]
    )
    review_exit = _read_text(run_dir / "openhands-review-exit-code.txt")
    if review_exit is not None:
        sections.append(f"- Exit code: {review_exit}")

    manager_decisions = _format_manager_decisions_section(run_dir)
    if manager_decisions:
        sections.extend(["", "### Manager Decisions", manager_decisions])

    review_notes = format_hoca_review_notes_fragment(run_dir)
    if review_notes:
        sections.extend(["", "### Review & Arbitration Notes", review_notes])

    final_state_section = _format_final_state_section(run_dir)
    if final_state_section:
        sections.extend(["", "### Final State", final_state_section])

    sections.extend(["", "### Run Context", format_run_context_fragment(run_dir)])

    sections.extend(["", "### Merge Status", f"- {merge_status}"])
    merge_policy = run_dir / "merge-policy.txt"
    if merge_policy.is_file():
        sections.append(f"- Merge policy recorded at: {merge_policy}")

    sections.extend(["", "### Research Sources"])
    research = _read_lines(run_dir / "research-sources.txt")
    if research:
        sections.append(_bullet_list([_redact_secret_like_values(item) for item in research[:40]]))
    else:
        sections.append("- No external research sources used.")

    sections.extend(["", "### Notes"])
    risk_notes = _read_lines(run_dir / "risk-notes.txt")
    if risk_notes:
        sections.append(_bullet_list([_redact_secret_like_values(item) for item in risk_notes[:80]]))
    else:
        sections.append("- No risk notes recorded.")

    if fragments.get("changes"):
        sections.extend(["", "### Changes Detail", fragments["changes"]])

    artifact_links = _artifact_link_paths(run_dir)
    sections.extend(["", "### Artifact Links", _bullet_list(artifact_links)])

    report = "\n".join(sections).strip() + "\n"
    return _redact_secret_like_values(report)


def write_task_report(project_path: Path, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "task-report.md"
    report_path.write_text(
        build_task_report_markdown(project_path, run_dir),
        encoding="utf-8",
    )
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a HOCA task report.")
    parser.add_argument("project_path", type=Path)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    report_path = write_task_report(args.project_path.resolve(), args.run_dir.resolve())
    print(f"Task report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
