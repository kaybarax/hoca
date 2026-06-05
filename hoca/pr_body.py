"""Build PR body section fragments from HOCA run artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from hoca.contracts import HocaManagerDecision, HocaReviewFinding, HocaReviewReport, HocaTaskSpec
from hoca.review_gate import (
    ReviewGateError,
    ReviewGateResult,
    code_review_error_fragment,
    code_review_pr_fragment,
    try_resolve_review_gate,
)
from hoca.run_state import (
    current_round,
    current_run_round,
    read_optional_json,
    read_optional_report,
    sandbox_mode_for_run,
)

_SECRET_LIKE_TASK = re.compile(
    r"(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|bearer\s+[a-z0-9_-]{10,}|"
    r"password\s*=\s*\S+|"
    r"-----BEGIN\s+(RSA|OPENSSH|EC)\s+PRIVATE\s+KEY-----)",
    re.IGNORECASE,
)

# Matches absolute local filesystem paths that should not appear in public PR content.
_LOCAL_PATH_RE = re.compile(
    r"/(?:Users|home|root|private(?:/var)?|var/folders)/[^\s\"'`<>\[\](){}]*",
)

# After path substitution, drop lines whose only content is a label + placeholder.
# E.g. "- **Project**: <local-path>" or "Target repository: <local-path>" adds nothing.
# Requires a label prefix so bare "<local-path>" tokens in mid-sentence are kept.
_LOCAL_PATH_ONLY_LINE_RE = re.compile(
    r"^[-*]?\s*(?:\*\*[^*]+\*\*|[A-Za-z][A-Za-z\s]*):?\s+<local-path>\s*$",
)


def _sanitize_pr_text(text: str) -> str:
    """Replace absolute local filesystem paths with a safe placeholder,
    then drop lines that contained only a path (e.g. '- **Project**: <path>')."""
    text = _LOCAL_PATH_RE.sub("<local-path>", text)
    clean = [line for line in text.split("\n") if not _LOCAL_PATH_ONLY_LINE_RE.match(line.strip())]
    return "\n".join(clean)


def _bullet_list(items: list[str], *, empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def _load_review_report(run_dir: Path, round_number: int) -> HocaReviewReport | None:
    payload = read_optional_report(run_dir, "review_report", round_number=round_number)
    if not payload:
        return None
    return HocaReviewReport.from_dict(payload)


def _load_manager_decision(run_dir: Path, round_number: int) -> HocaManagerDecision | None:
    payload = read_optional_report(run_dir, "manager_decision", round_number=round_number)
    if not payload:
        return None
    return HocaManagerDecision.from_dict(payload)


def _findings_by_id_from_reports(run_dir: Path) -> dict[str, HocaReviewFinding]:
    findings: dict[str, HocaReviewFinding] = {}
    review_round = current_round(run_dir, prefix="review-report-", subdir="reviews")
    for round_number in range(1, review_round + 1):
        report = _load_review_report(run_dir, round_number)
        if report is None:
            continue
        for finding in report.findings:
            findings[finding.id] = finding
    return findings


def _repair_accepted_finding_ids(run_dir: Path) -> set[str]:
    accepted: set[str] = set()
    decision_round = current_round(run_dir, prefix="manager-decision-", subdir="decisions")
    for round_number in range(1, decision_round + 1):
        decision = _load_manager_decision(run_dir, round_number)
        if decision is None or decision.decision != "repair_required":
            continue
        accepted.update(decision.accepted_findings)
    return accepted


def _latest_review_round(run_dir: Path) -> int:
    return current_round(run_dir, prefix="review-report-", subdir="reviews") or 1


def _latest_manager_decision(run_dir: Path) -> HocaManagerDecision | None:
    decision_round = current_round(run_dir, prefix="manager-decision-", subdir="decisions")
    if not decision_round:
        return None
    return _load_manager_decision(run_dir, decision_round)


def _aggregate_manager_lists(
    run_dir: Path,
    field: str,
) -> list[str]:
    decision_round = current_round(run_dir, prefix="manager-decision-", subdir="decisions")
    seen: list[str] = []
    for round_number in range(1, decision_round + 1):
        decision = _load_manager_decision(run_dir, round_number)
        if decision is None:
            continue
        for item in getattr(decision, field):
            if item not in seen:
                seen.append(item)
    return seen


def _format_finding_line(
    finding_id: str,
    findings_by_id: dict[str, HocaReviewFinding],
    *,
    suffix: str = "",
) -> str:
    finding = findings_by_id.get(finding_id)
    if finding is None:
        return f"{finding_id}{suffix}"
    location = f" ({finding.file})" if finding.file else ""
    detail = finding.summary
    if finding.required_fix:
        detail = f"{finding.summary} — {finding.required_fix}"
    return f"{finding.id}{location}: {detail}{suffix}"


def _fixed_finding_lines(run_dir: Path) -> list[str]:
    findings_by_id = _findings_by_id_from_reports(run_dir)
    repaired = _repair_accepted_finding_ids(run_dir)
    if not repaired:
        return []

    latest_report = _load_review_report(run_dir, _latest_review_round(run_dir))
    open_ids = (
        {finding.id for finding in latest_report.findings} if latest_report is not None else set()
    )
    fixed_ids = sorted(repaired - open_ids)
    return [_format_finding_line(finding_id, findings_by_id) for finding_id in fixed_ids]


def _draft_pr_flag(run_dir: Path) -> dict[str, object] | None:
    flag_path = run_dir / "draft-pr-with-blockers.flag"
    if not flag_path.is_file():
        return None
    try:
        payload = json.loads(flag_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_draft_pr_run(run_dir: Path) -> bool:
    if _draft_pr_flag(run_dir) is not None:
        return True
    decision = _latest_manager_decision(run_dir)
    return decision is not None and decision.decision == "draft_pr_with_blockers"


def human_attention_required_for_run(run_dir: Path) -> bool:
    """Return whether the run outcome requires human attention before merge."""
    status = read_optional_json(run_dir / "status.json") or {}
    human_required = bool(status.get("human_attention_required"))

    decision = _latest_manager_decision(run_dir)
    if decision is not None:
        human_required = human_required or decision.human_attention_required

    spec_path = run_dir / "task-spec.json"
    if spec_path.is_file():
        try:
            spec = HocaTaskSpec.from_json(spec_path.read_text(encoding="utf-8"))
            human_required = human_required or spec.requires_human_approval
        except ValueError:
            pass

    if is_draft_pr_run(run_dir):
        human_required = True

    return human_required


def unresolved_findings_for_run(run_dir: Path) -> list[HocaReviewFinding]:
    """Return structured findings still unresolved at run completion."""
    findings_by_id = _findings_by_id_from_reports(run_dir)
    unresolved_ids: set[str] = set()

    latest_review = _load_review_report(run_dir, _latest_review_round(run_dir))
    if latest_review is not None:
        unresolved_ids.update(finding.id for finding in latest_review.findings)

    for finding_id in _aggregate_manager_lists(run_dir, "downgraded_to_pr_notes"):
        unresolved_ids.add(finding_id)

    decision = _latest_manager_decision(run_dir)
    if decision is not None and decision.decision == "draft_pr_with_blockers":
        for finding_id in decision.accepted_findings:
            if finding_id in findings_by_id and findings_by_id[finding_id].severity == "medium":
                unresolved_ids.add(finding_id)

    if is_draft_pr_run(run_dir):
        flag = _draft_pr_flag(run_dir) or {}
        for finding_id in flag.get("accepted_findings", []):
            if isinstance(finding_id, str):
                unresolved_ids.add(finding_id)

    return [
        findings_by_id[finding_id]
        for finding_id in sorted(unresolved_ids)
        if finding_id in findings_by_id
    ]


def format_task_spec_fragment(run_dir: Path, *, task_oneline: str) -> str:
    spec_path = run_dir / "task-spec.json"
    if not spec_path.is_file():
        return (
            f"**Goal**: {task_oneline}\n\n"
            "_No structured `task-spec.json` found in the run directory._"
        )

    try:
        spec = HocaTaskSpec.from_json(spec_path.read_text(encoding="utf-8"))
    except ValueError:
        return f"**Goal**: {task_oneline}\n\n_Task spec present but could not be parsed safely._"

    goal_first_line = spec.goal.split("\n\n")[0].split("\n")[0].strip()
    goal_display = _sanitize_pr_text(goal_first_line)
    lines = [
        f"**Goal**: {goal_display}",
        f"**Risk level**: {spec.risk_level}",
    ]
    if spec.expected_areas:
        lines.append(f"**Expected areas**: {', '.join(spec.expected_areas)}")
    if spec.acceptance_criteria:
        lines.append("")
        lines.append("**Acceptance criteria**:")
        lines.extend(f"- {item}" for item in spec.acceptance_criteria[:8])
        if len(spec.acceptance_criteria) > 8:
            lines.append(f"- _({len(spec.acceptance_criteria) - 8} more omitted)_")
    if spec.non_goals:
        lines.append("")
        lines.append("**Non-goals**:")
        lines.extend(f"- {item}" for item in spec.non_goals[:5])
    return "\n".join(lines)


def _resolve_review_gate(run_dir: Path) -> tuple[ReviewGateResult | None, bool]:
    review_round = _latest_review_round(run_dir)
    try:
        result = try_resolve_review_gate(run_dir, round_number=review_round)
    except ReviewGateError:
        return None, True
    return result, False


def format_hoca_review_notes_fragment(run_dir: Path) -> str:
    review_round = _latest_review_round(run_dir)
    run_round = current_run_round(run_dir) or review_round
    review_result, review_gate_error = _resolve_review_gate(run_dir)
    findings_by_id = _findings_by_id_from_reports(run_dir)
    manager_decision = _latest_manager_decision(run_dir)
    latest_review = _load_review_report(run_dir, review_round)

    lines: list[str] = []

    if is_draft_pr_run(run_dir):
        lines.extend(
            [
                "> **DRAFT PR — residual findings remain**",
                "> HOCA opened this pull request as a **draft** after the round cap. "
                "Medium-severity findings below still need human review before merge.",
                "",
            ]
        )

    if review_gate_error:
        lines.append(code_review_error_fragment())
    elif review_result is None:
        lines.append("_No review artifacts found in the run directory._")
    else:
        report = review_result.report
        lines.append(f"**Reviewer verdict**: {report.verdict}")
        lines.append(f"**Review round**: {review_round} of {run_round}")
        lines.append("")
        lines.append(code_review_pr_fragment(review_result))

    lines.append("")
    lines.append("**Accepted findings fixed**:")
    fixed_lines = _fixed_finding_lines(run_dir)
    lines.append(
        _bullet_list(
            fixed_lines,
            empty="_None recorded — no prior repair rounds or all accepted items remain open._",
        )
    )

    rejected_ids = _aggregate_manager_lists(run_dir, "rejected_findings")
    downgraded_ids = _aggregate_manager_lists(run_dir, "downgraded_to_pr_notes")
    residual_ids: list[str] = []
    if manager_decision is not None and manager_decision.decision == "draft_pr_with_blockers":
        residual_ids = [
            finding_id
            for finding_id in manager_decision.accepted_findings
            if finding_id in findings_by_id and findings_by_id[finding_id].severity == "medium"
        ]

    lines.append("")
    lines.append("**Reviewer proposals intentionally not fixed**:")
    rejected_lines = [
        _format_finding_line(finding_id, findings_by_id, suffix=" (rejected by manager)")
        for finding_id in rejected_ids
    ]
    lines.append(
        _bullet_list(
            rejected_lines,
            empty="_None — manager did not reject reviewer proposals for this publication._",
        )
    )

    lines.append("")
    lines.append("**Downgraded PR tech debt**:")
    downgraded_lines = [
        _format_finding_line(finding_id, findings_by_id, suffix=" (deferred to PR follow-up)")
        if finding_id in findings_by_id
        else f"{finding_id} (deferred to PR follow-up)"
        for finding_id in downgraded_ids
    ]
    if latest_review is not None:
        for note in latest_review.pr_notes.get("known_followups", []):
            if note not in downgraded_lines:
                downgraded_lines.append(note)
    lines.append(
        _bullet_list(
            downgraded_lines,
            empty="_None — no findings were downgraded to PR follow-up._",
        )
    )

    if residual_ids or is_draft_pr_run(run_dir):
        lines.append("")
        lines.append("**Residual medium findings (draft PR)**:")
        residual_lines = [
            _format_finding_line(
                finding_id,
                findings_by_id,
                suffix=" — unresolved at round cap",
            )
            for finding_id in residual_ids
        ]
        if not residual_lines and is_draft_pr_run(run_dir):
            flag = _draft_pr_flag(run_dir) or {}
            for finding_id in flag.get("accepted_findings", []):
                if isinstance(finding_id, str):
                    residual_lines.append(
                        _format_finding_line(
                            finding_id,
                            findings_by_id,
                            suffix=" — flagged in draft-pr-with-blockers",
                        )
                    )
        lines.append(
            _bullet_list(
                residual_lines,
                empty="_Draft PR flagged, but no medium finding IDs were recorded._",
            )
        )

    if latest_review is not None and latest_review.pr_notes.get("summary"):
        lines.append("")
        lines.append("**Reviewer summary notes**:")
        lines.extend(f"- {note}" for note in latest_review.pr_notes["summary"])

    return "\n".join(lines).strip()


def format_run_context_fragment(run_dir: Path) -> str:
    sandbox_mode = sandbox_mode_for_run(run_dir)
    status = read_optional_json(run_dir / "status.json") or {}
    human_required = human_attention_required_for_run(run_dir)

    auto_merge = status.get("auto_merge_queued") or status.get("merge_performed")
    review_round = _latest_review_round(run_dir)

    lines = [
        f"- **Sandbox mode**: {sandbox_mode}",
        f"- **Human review required before merge**: {'yes' if human_required else 'no'}",
        f"- **HOCA review rounds completed**: {review_round}",
    ]
    if is_draft_pr_run(run_dir):
        lines.append("- **Publication mode**: draft PR (residual findings documented above)")
    if auto_merge:
        lines.append("- **Auto-merge**: queued or completed by HOCA policy")
    else:
        lines.append("- **Auto-merge**: not queued (human merge expected)")
    return "\n".join(lines)


def summarize_pr_body_fragments(
    run_dir: Path,
    *,
    task: str,
    issue_id: str | None = None,
    changes: str | None = None,
) -> dict[str, str]:
    """Return PR template slug -> markdown fragment for a HOCA run."""
    from hoca.run_state import summarize_run_for_pr_body

    # Use only the first paragraph/line of the task text so execution-context
    # metadata like "Target repository: /path" is never folded into the summary.
    task_first_line = task.split("\n\n")[0].split("\n")[0].strip()
    task_oneline = _sanitize_pr_text(" ".join(task_first_line.split()))

    base = summarize_run_for_pr_body(run_dir, task=task, issue_id=issue_id)
    # Sanitize any absolute local paths that appear in run_state-generated fragments
    # (e.g. worktree paths in tests-summary.md).
    base = {k: _sanitize_pr_text(v) for k, v in base.items()}
    # Replace the full-task-text summary (set by run_state) with the cleaned first-line version.
    base["summary"] = task_oneline

    if changes is not None:
        base["changes"] = changes

    base["task-spec"] = format_task_spec_fragment(run_dir, task_oneline=task_oneline)
    base["hoca-review-notes"] = format_hoca_review_notes_fragment(run_dir)
    base["run-context"] = format_run_context_fragment(run_dir)

    if not base["summary"].startswith(">"):
        spec_path = run_dir / "task-spec.json"
        if spec_path.is_file():
            try:
                spec = HocaTaskSpec.from_json(spec_path.read_text(encoding="utf-8"))
                # Use only the first line of spec.goal (sanitized) as supplemental
                # summary text — never embed the full raw task prompt.
                goal_first_line = spec.goal.split("\n\n")[0].split("\n")[0].strip()
                goal_summary = _sanitize_pr_text(" ".join(goal_first_line.split()))
                if goal_summary and goal_summary != task_oneline:
                    base["summary"] = f"{task_oneline}\n\n{goal_summary}"
            except ValueError:
                pass

    return base


def write_pr_body_fragment_files(
    run_dir: Path,
    fragments: dict[str, str],
) -> list[Path]:
    written: list[Path] = []
    for slug, content in fragments.items():
        path = run_dir / f"pr-fragment-{slug}.txt"
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write HOCA PR body fragment files.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--issue-id", default="")
    parser.add_argument(
        "--changes-file",
        type=Path,
        help="Optional pre-built Changes section (e.g. git log from create-pr.sh).",
    )
    args = parser.parse_args(argv)

    if _SECRET_LIKE_TASK.search(args.task):
        print("Task text looks secret-like; refusing to build PR fragments.", file=sys.stderr)
        return 2

    run_dir = args.run_dir.resolve()
    changes = None
    if args.changes_file is not None and args.changes_file.is_file():
        changes = args.changes_file.read_text(encoding="utf-8").strip()

    issue_id = args.issue_id.strip() or None
    fragments = summarize_pr_body_fragments(
        run_dir,
        task=args.task,
        issue_id=issue_id,
        changes=changes,
    )
    paths = write_pr_body_fragment_files(run_dir, fragments)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
