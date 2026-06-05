from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from hoca.config import PolicyError
from hoca.fleet_contracts import FleetReadinessState, HocaMergeReadiness
from hoca.git_utils import current_branch
from hoca.run_state import now_iso, read_optional_json, write_json_atomic
from hoca.security import is_secret_like_path


@dataclass(frozen=True)
class LocalReadinessInputs:
    lane_id: str
    run_dir: Path
    repo_path: Path
    base_ref: str | None = None


@dataclass(frozen=True)
class MergeRepairPlan:
    readiness: str
    reason: str | None
    escalate_to_human: bool
    can_auto_repair: bool
    repair_brief_path: Path | None
    repair_report_path: Path | None


MERGE_REPAIR_BRIEF_PATH = "merge-repair-brief.json"
MERGE_REPAIR_REPORT_PATH = "merge_conflict_report.txt"
MERGE_REPAIR_HIGH_RISK_HINTS = (
    "/migrations/",
    "/schema/",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "requirements.txt",
    "go.mod",
    "go.sum",
    "cargo.lock",
    "/api/",
    "/openapi/",
    "/openapi.yaml",
    "/openapi.yml",
)
_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _line_list(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _collect_changed_files(run_dir: Path) -> list[str]:
    for path in (
        run_dir / "changed-files-after-openhands.txt",
        run_dir / "changed-files.txt",
    ):
        items = _line_list(path)
        if items:
            return items
    return []


def _collect_reviewed_files(run_dir: Path) -> list[str]:
    for path in (
        run_dir / "review" / "changed-files.txt",
        run_dir / "review" / "changed-files-after-openhands.txt",
    ):
        items = _line_list(path)
        if items:
            return items
    return []


def _run_raw_command(repo_path: Path, args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError as exc:
        raise PolicyError(f"Git command failed: {exc}") from exc
    return result.returncode, result.stdout.strip()


def _select_base_ref(run_dir: Path, base_ref: str | None) -> str | None:
    if base_ref:
        return base_ref
    payload = read_optional_json(run_dir / "status.json")
    if not isinstance(payload, dict):
        return None
    for key in ("base_ref", "base_branch", "task_base_branch"):
        value = payload.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return None


def _normalized_branch_name(repo_path: Path) -> str | None:
    try:
        branch = current_branch(repo_path)
    except PolicyError:
        return None
    if not branch or branch == "HEAD":
        return None
    return branch


def _has_conflict_markers(repo_path: Path, changed_files: list[str]) -> bool:
    for candidate in changed_files:
        path = (repo_path / candidate).resolve()
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(marker in text for marker in _CONFLICT_MARKERS):
            return True
    return False


def _is_high_risk_file(path: str) -> bool:
    normalized = f"/{path.strip().lower()}"
    return any(hint in normalized for hint in MERGE_REPAIR_HIGH_RISK_HINTS)


def _write_conflict_report(
    run_dir: Path,
    inputs: LocalReadinessInputs,
    base_ref: str,
    changed_files: list[str],
    ready_to_escalate: bool,
) -> Path:
    path = run_dir / MERGE_REPAIR_REPORT_PATH
    lines = [
        f"lane_id: {inputs.lane_id}",
        f"base_ref: {base_ref}",
        "status: merge_conflict",
        f"risk: {'high' if ready_to_escalate else 'standard'}",
        "changed_files:",
    ]
    lines.extend(f"- {item}" for item in changed_files)
    lines.append("resolution: requires manual reconciliation")
    write_text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(write_text, encoding="utf-8")
    return path


def _write_repair_brief(
    run_dir: Path,
    inputs: LocalReadinessInputs,
    base_ref: str,
    readiness: str,
    changed_files: list[str],
    escalate_to_human: bool,
    conflict_markers_seen: bool,
) -> tuple[Path, Path | None]:
    path = run_dir / MERGE_REPAIR_BRIEF_PATH
    report_path = None
    if readiness == "merge_conflict":
        report_path = _write_conflict_report(
            run_dir, inputs, base_ref, changed_files, escalate_to_human
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "lane_id": inputs.lane_id,
        "base_ref": base_ref,
        "readiness_state": readiness,
        "changed_files": changed_files,
        "escalate_to_human": escalate_to_human,
        "conflict_markers_seen": conflict_markers_seen,
        "created_at": now_iso(),
    }
    write_json_atomic(path, payload)
    return path, report_path


def evaluate_merge_repair_plan(inputs: LocalReadinessInputs) -> MergeRepairPlan:
    run_dir = inputs.run_dir.resolve()
    repo_path = inputs.repo_path.resolve()
    changed_files = _collect_changed_files(run_dir)
    base_ref = _select_base_ref(run_dir, inputs.base_ref)

    if base_ref is None:
        return MergeRepairPlan(
            readiness="not_ready",
            reason="Target base reference is missing.",
            escalate_to_human=True,
            can_auto_repair=False,
            repair_brief_path=None,
            repair_report_path=None,
        )

    return_code, _ = _run_raw_command(repo_path, ["merge-base", "--is-ancestor", base_ref, "HEAD"])
    if return_code == 0:
        return MergeRepairPlan(
            readiness="ready",
            reason=None,
            escalate_to_human=False,
            can_auto_repair=False,
            repair_brief_path=None,
            repair_report_path=None,
        )

    conflict_markers_seen = _has_conflict_markers(repo_path, changed_files)
    readiness = "merge_conflict" if conflict_markers_seen else "needs_rebase"
    escalate_to_human = readiness == "merge_conflict" and any(
        _is_high_risk_file(item) for item in changed_files
    )
    can_auto_repair = readiness == "needs_rebase"
    reason = (
        "Merge conflict markers were detected while checking divergence."
        if conflict_markers_seen
        else "Branch is not an ancestor of the base branch; rebase or branch update is required."
    )
    brief_path, report_path = _write_repair_brief(
        run_dir,
        inputs,
        base_ref,
        readiness,
        changed_files,
        escalate_to_human,
        conflict_markers_seen,
    )
    return MergeRepairPlan(
        readiness=readiness,
        reason=reason,
        escalate_to_human=escalate_to_human,
        can_auto_repair=can_auto_repair,
        repair_brief_path=brief_path,
        repair_report_path=report_path,
    )


def send_merge_repair_through_adapter(
    inputs: LocalReadinessInputs,
    *,
    sender: Callable[[str, Path], bool] | None = None,
) -> bool:
    plan = evaluate_merge_repair_plan(inputs)
    if not plan.can_auto_repair:
        return False
    if sender is None or plan.repair_brief_path is None:
        return False
    return bool(sender(inputs.lane_id, plan.repair_brief_path))


def evaluate_local_merge_readiness(inputs: LocalReadinessInputs) -> HocaMergeReadiness:
    run_dir = inputs.run_dir.resolve()
    repo_path = inputs.repo_path.resolve()

    changed_files = _collect_changed_files(run_dir)
    reviewed_files = _collect_reviewed_files(run_dir)
    checks: list[str] = []
    issues: list[str] = []
    status: FleetReadinessState = "ready"

    branch = _normalized_branch_name(repo_path)
    checks.append("branch_base")
    if branch is None:
        issues.append("Task branch could not be determined (detached HEAD).")
        status = "blocked"

    checks.append("diff_check")
    return_code, details = _run_raw_command(repo_path, ["diff", "--check", "--cached"])
    if return_code != 0:
        issues.append(f"git diff --check reported issues: {details}")
        status = "blocked"

    base_ref = _select_base_ref(run_dir, inputs.base_ref)
    checks.append("merge_base")
    if base_ref is None:
        issues.append("Target base reference is missing.")
        status = "not_ready"
    else:
        return_code, output = _run_raw_command(
            repo_path, ["merge-base", "--is-ancestor", base_ref, "HEAD"]
        )
        if return_code != 0:
            repair_plan = evaluate_merge_repair_plan(
                LocalReadinessInputs(
                    lane_id=inputs.lane_id,
                    run_dir=run_dir,
                    repo_path=repo_path,
                    base_ref=base_ref,
                )
            )
            checks.append("merge_repair")
            issues.append(output or "Unable to confirm merge base ancestry.")
            issues.append("Base branch diverged from HEAD; rebase or branch update is required.")
            if repair_plan.reason:
                issues.append(repair_plan.reason)
            if repair_plan.escalate_to_human:
                issues.append("merge_conflict touches high-risk files; escalate to human review.")
            status = "blocked"
        else:
            status = "ready" if status == "ready" else status

    checks.append("diff_exists")
    if base_ref is None:
        issues.append("Cannot validate merge diff without a base reference.")
        status = "not_ready" if status == "ready" else status
    else:
        return_code, changed_from_base = _run_raw_command(
            repo_path, ["diff", "--name-only", f"{base_ref}...HEAD"]
        )
        if return_code != 0:
            issues.append(f"git diff --name-only failed for {base_ref}...HEAD: {changed_from_base}")
            status = "blocked"
        elif not changed_from_base:
            issues.append("No files changed against the target base branch.")
            status = "not_ready" if status == "ready" else status

    checks.append("reviewed_files")
    if not reviewed_files:
        issues.append("No reviewed-file evidence found.")
        status = "blocked" if status == "ready" else status
    else:
        unreviewed = [path for path in changed_files if path not in reviewed_files]
        if unreviewed:
            issues.append(
                "Changed files not present in reviewed file list: "
                + ", ".join(json.dumps(unreviewed))
            )
            status = "blocked"

    checks.append("secret_like_paths")
    secret_paths = [path for path in changed_files if is_secret_like_path(path)]
    if secret_paths:
        issues.append("Secret-like files were changed: " + ", ".join(secret_paths))
        status = "blocked"

    if status == "ready" and not changed_files:
        status = "not_ready"
        issues.append("No candidate changed files were discovered in the run artifacts.")

    return HocaMergeReadiness(
        lane_id=inputs.lane_id,
        readiness=status,
        checks=checks,
        reason="; ".join(issues) if issues else None,
        checked_at=now_iso(),
        human_review_required=True,
    )
