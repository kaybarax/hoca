from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hoca.fleet_contracts import FleetReadinessState, HocaMergeReadiness
from hoca.run_state import now_iso, read_optional_json


@dataclass(frozen=True)
class PrReadinessInputs:
    lane_id: str
    run_dir: Path
    require_ui_screenshot: bool = False
    screenshot_path_patterns: tuple[str, ...] = ("screenshot", "screenshots")
    ui_file_extensions: tuple[str, ...] = (
        ".css",
        ".html",
        ".jsx",
        ".js",
        ".tsx",
        ".ts",
        ".vue",
        ".svelte",
    )


def _artifact_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _read_changed_files(run_dir: Path) -> list[str]:
    candidates = (
        run_dir / "changed-files-after-openhands.txt",
        run_dir / "changed-files.txt",
    )
    for path in candidates:
        text = _artifact_text(path)
        if text:
            return [line.strip() for line in text.splitlines() if line.strip()]
    return []


def _read_pr_reference(run_dir: Path) -> str | None:
    pr_url_path = run_dir / "pr-url.txt"
    pr_url = _artifact_text(pr_url_path)
    if pr_url:
        return pr_url

    status = read_optional_json(run_dir / "status.json")
    if isinstance(status, dict):
        for key in ("pr_url", "prUrl", "pr_number", "pr"):
            value = status.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value
            if isinstance(value, int):
                return str(value)
    return None


def _run_gh_json(command: list[str]) -> list[dict] | dict[str, object] | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _is_ui_related(paths: list[str], *, extensions: tuple[str, ...]) -> bool:
    if not paths:
        return False
    lower_extensions = {item.lower() for item in extensions}
    return any(Path(path).suffix.lower() in lower_extensions for path in paths)


def _has_screenshot_artifact(run_dir: Path, *, hints: tuple[str, ...]) -> bool:
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        if any(hint in lower for hint in hints):
            return True
    return False


def _classify_pr_checks(payload: object) -> str:
    if not isinstance(payload, list):
        return "unknown"
    if not payload:
        return "unknown"

    conclusions = [str(item.get("conclusion", "")) for item in payload if isinstance(item, dict)]
    statuses = [str(item.get("status", "")) for item in payload if isinstance(item, dict)]
    if not conclusions:
        return "unknown"

    if any(item in {"in_progress", "queued", "pending"} for item in statuses):
        return "pending"
    if any(
        item in {"failure", "cancelled", "timed_out", "action_required"} for item in conclusions
    ):
        return "failed"
    if all(item in {"success", "neutral", "skipped"} for item in conclusions):
        return "passed"
    return "unknown"


def _classify_pr_view(payload: object) -> tuple[str, str | None, str | None]:
    if not isinstance(payload, dict):
        return "blocked", None, "Unable to read PR metadata from gh output."

    state = str(payload.get("state", "")).lower()
    if state and state != "open":
        return "blocked", None, f"PR is not open (state={state})."

    if payload.get("isDraft"):
        return "not_ready", "draft", "PR is still a draft."

    mergeable = payload.get("mergeable")
    if mergeable is False:
        return "blocked", "mergeable", "PR is not mergeable on GitHub."

    merge_state = str(payload.get("mergeStateStatus", "")).upper()
    if merge_state and merge_state not in {"CLEAN", "MERGEABLE"}:
        return "blocked", "merge_state", f"PR merge state is {merge_state}."

    review_decision = (
        str(payload.get("reviewDecision", "")).upper() if payload.get("reviewDecision") else ""
    )
    if review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        return "draft_ready", "review", "PR has pending review findings."

    if review_decision == "APPROVED":
        return "ready", None, None

    return "ready", None, None


def evaluate_pr_merge_readiness(inputs: PrReadinessInputs) -> HocaMergeReadiness:
    run_dir = inputs.run_dir.resolve()
    checks: list[str] = []
    issues: list[str] = []
    status: FleetReadinessState = "not_ready"

    changed_files = _read_changed_files(run_dir)
    pr_ref = _read_pr_reference(run_dir)
    checks.append("pr_reference")
    if not pr_ref:
        issues.append("No PR URL/number found in run artifacts.")
        return HocaMergeReadiness(
            lane_id=inputs.lane_id,
            readiness="not_ready",
            ci_status="missing",
            pr_url=None,
            checks=checks,
            reason="; ".join(issues),
            checked_at=now_iso(),
            human_review_required=True,
        )

    checks.append("pr_checks")
    check_payload = _run_gh_json(["gh", "pr", "checks", pr_ref, "--json", "conclusion,status,name"])
    check_status = _classify_pr_checks(check_payload)
    if check_status == "pending":
        status = "not_ready"
        issues.append("PR checks are still pending.")
    elif check_status == "failed":
        status = "blocked"
        issues.append("PR checks failed.")
    elif check_status == "unknown":
        status = "blocked"
        issues.append("Unable to classify PR checks.")
    elif check_status == "passed":
        status = "ready"
    else:
        status = "blocked"
        issues.append("Unable to classify PR checks.")

    checks.append("pr_view")
    pr_view_payload = _run_gh_json(
        [
            "gh",
            "pr",
            "view",
            pr_ref,
            "--json",
            "state,isDraft,mergeStateStatus,mergeable,reviewDecision",
        ]
    )
    view_status, _, view_reason = _classify_pr_view(pr_view_payload)
    if view_status == "ready":
        pass
    elif view_status == "not_ready":
        if status not in {"blocked"}:
            status = "not_ready"
        issues.append(view_reason or "PR is not merge-ready yet.")
    elif view_status == "draft_ready":
        checks.append("draft_ready")
        if status == "ready":
            status = "draft_ready"
        issues.append(view_reason or "PR needs human review attention.")
    else:
        if status != "blocked":
            status = "blocked"
        issues.append(view_reason or "PR metadata blocked readiness.")

    if inputs.require_ui_screenshot and _is_ui_related(
        changed_files,
        extensions=inputs.ui_file_extensions,
    ):
        checks.append("screenshot")
        if not _has_screenshot_artifact(run_dir, hints=inputs.screenshot_path_patterns):
            if status in {"ready", "draft_ready"}:
                status = "blocked"
            issues.append("UI changes require a screenshot artifact before merge.")

    if status == "ready":
        issues = []
    return HocaMergeReadiness(
        lane_id=inputs.lane_id,
        readiness=status,
        ci_status=check_status,
        pr_url=pr_ref,
        checks=checks,
        reason="; ".join(issues) if issues else None,
        checked_at=now_iso(),
        human_review_required=status != "ready",
    )
