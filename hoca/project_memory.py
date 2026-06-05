from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.context_pack import append_failure_pattern
from hoca.control_paths import make_fleet_control_paths
from hoca.run_state import current_round, now_iso, read_optional_json, write_json_atomic


@dataclass(frozen=True)
class LaneReward:
    project_id: str
    lane_id: str
    readiness: str
    ci_passed: bool
    review_passed: bool
    human_merged: bool
    blocked_reasons: tuple[str, ...]
    prompt_patterns: tuple[str, ...]
    created_at: str


PASS_REVIEW_VERDICTS = frozenset({"lgtm", "pass", "approved", "ready"})
REVIEW_VERDICT_UNKNOWN = "needs_work"


def _read_validation_report(run_dir: Path) -> dict[str, Any] | None:
    round_number = current_round(run_dir, prefix="validation-report-", subdir="validation")
    if round_number == 0:
        return None
    return read_optional_json(run_dir / "validation" / f"validation-report-{round_number}.json")


def _read_review_report(run_dir: Path) -> dict[str, Any] | None:
    round_number = current_round(run_dir, prefix="review-report-", subdir="reviews")
    if round_number == 0:
        return None
    return read_optional_json(run_dir / "reviews" / f"review-report-{round_number}.json")


def _read_status_payload(run_dir: Path) -> dict[str, Any]:
    return read_optional_json(run_dir / "status.json") or {}


def _read_final_state(run_dir: Path) -> dict[str, Any] | None:
    return read_optional_json(run_dir / "final-state.json")


def _safe_prompt_line(raw: str) -> str:
    line = raw.strip().splitlines()
    first_line = next((item.strip() for item in line if item.strip()), "")
    if not first_line:
        return ""
    return re.sub(r"\s+", " ", first_line)[:200]


def _collect_prompt_patterns(run_dir: Path) -> tuple[str, ...]:
    status = _read_status_payload(run_dir)
    patterns: list[str] = []

    attempt_round = current_round(run_dir, prefix="worker-attempt-", subdir="attempts")
    if attempt_round:
        worker_prompt = run_dir / f"worker-hermes-prompt-round-{attempt_round}.txt"
        if worker_prompt.is_file():
            first = _safe_prompt_line(worker_prompt.read_text(encoding="utf-8"))
            if first:
                patterns.append(f"worker:{first}")
        reviewer_prompt = run_dir / f"reviewer-hermes-prompt-round-{attempt_round}.txt"
        if reviewer_prompt.is_file():
            first = _safe_prompt_line(reviewer_prompt.read_text(encoding="utf-8"))
            if first:
                patterns.append(f"reviewer:{first}")

    if status.get("prompt_patterns") and isinstance(status.get("prompt_patterns"), list):
        patterns.extend(str(item) for item in status["prompt_patterns"] if str(item).strip())

    deduped: list[str] = []
    for item in patterns:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _infer_human_merged(status: dict[str, Any], final_state: dict[str, Any] | None) -> bool:
    final_status = str(final_state.get("status", "")) if isinstance(final_state, dict) else ""
    if final_status in {"pr_opened", "draft_pr_opened", "ready_for_human"}:
        return True
    status_state = str(status.get("status", "")).strip().lower()
    if status_state in {"ready_for_human", "needs_human_staging"}:
        return True
    return False


def _append_blocked_reason(blocked_reasons: list[str], *, prefix: str, value: object) -> None:
    text = str(value).strip() if value is not None else ""
    if text:
        blocked_reasons.append(f"{prefix}:{text}")


def _infer_ci_passed(validation: dict[str, Any], blocked_reasons: list[str]) -> bool:
    tests_passed = bool(validation.get("tests_passed", False))
    if not tests_passed:
        _append_blocked_reason(blocked_reasons, prefix="validation", value="tests_failed")

    secret_scan_clean = bool(validation.get("secret_scan_clean", True))
    if not secret_scan_clean:
        _append_blocked_reason(blocked_reasons, prefix="validation", value="secret_scan_detected")

    monitor_clean = bool(validation.get("monitor_clean", True))
    if not monitor_clean:
        _append_blocked_reason(blocked_reasons, prefix="validation", value="monitor_not_clean")

    for blocker in validation.get("hard_blockers", ()) if isinstance(validation.get("hard_blockers"), list) else ():
        if isinstance(blocker, str) and blocker.strip():
            _append_blocked_reason(blocked_reasons, prefix="validation", value=blocker)

    return not blocked_reasons and tests_passed and secret_scan_clean and monitor_clean


def _infer_review_passed(review_report: dict[str, Any] | None, *, blocked_reasons: list[str]) -> bool:
    if review_report is None:
        return False
    verdict = str(review_report.get("verdict", REVIEW_VERDICT_UNKNOWN)).strip().lower()
    if verdict in PASS_REVIEW_VERDICTS:
        return True
    _append_blocked_reason(blocked_reasons, prefix="review", value=verdict)
    return False


def infer_lane_reward_from_run_dir(
    project_id: str,
    lane_id: str,
    run_dir: Path,
    *,
    readiness: str,
    control_root: Path | None = None,
) -> LaneReward:
    del control_root
    status = _read_status_payload(run_dir)
    final_state = _read_final_state(run_dir)
    validation = _read_validation_report(run_dir)
    review_report = _read_review_report(run_dir)

    blocked_reasons: list[str] = []

    if validation is None:
        blocked_reasons.append("validation.report_missing")
        ci_passed = False
    else:
        ci_passed = _infer_ci_passed(validation, blocked_reasons=blocked_reasons)

    if review_report is not None:
        review_passed = _infer_review_passed(review_report, blocked_reasons=blocked_reasons)
    else:
        review_review_path = run_dir / "openhands-review.txt"
        if review_review_path.is_file():
            raw = review_review_path.read_text(encoding="utf-8")
            review_passed = "lgtm" in raw.lower()
            if not review_passed:
                _append_blocked_reason(blocked_reasons, prefix="review", value="no_structured_report")
        else:
            review_passed = False

    status_reason = status.get("reason")
    if status_reason and str(status_reason).strip():
        blocked_reasons.append(f"run.reason:{str(status_reason).strip()}")

    status_state = str(status.get("status", "")).strip().lower()
    if status_state in {"blocked", "failed", "needs_human_staging"}:
        blocked_reasons.append(f"run.status:{status_state}")

    return LaneReward(
        project_id=project_id,
        lane_id=lane_id,
        readiness=readiness,
        ci_passed=ci_passed and status_state not in {"blocked", "failed", "needs_human_staging"},
        review_passed=review_passed and status_state not in {"blocked", "failed", "needs_human_staging"},
        human_merged=_infer_human_merged(status, final_state),
        blocked_reasons=tuple(blocked_reasons),
        prompt_patterns=_collect_prompt_patterns(run_dir),
        created_at=now_iso(),
    )


def record_lane_reward_from_run_dir(
    project_id: str,
    lane_id: str,
    run_dir: Path,
    *,
    readiness: str,
    control_root: Path | None = None,
) -> Path:
    inferred = infer_lane_reward_from_run_dir(
        project_id,
        lane_id,
        run_dir,
        readiness=readiness,
        control_root=control_root,
    )
    return record_lane_reward(
        project_id=project_id,
        lane_id=lane_id,
        readiness=readiness,
        ci_passed=inferred.ci_passed,
        review_passed=inferred.review_passed,
        human_merged=inferred.human_merged,
        blocked_reasons=inferred.blocked_reasons,
        prompt_patterns=inferred.prompt_patterns,
        control_root=control_root,
    )


def _memory_dir(project_id: str, *, control_root: Path | None = None) -> Path:
    return make_fleet_control_paths(override=control_root).memory_dir / project_id / "fleet-rewards"


def _rewards_path(project_id: str, *, control_root: Path | None = None) -> Path:
    return _memory_dir(project_id, control_root=control_root) / "lane-rewards.jsonl"


def _as_entry(payload: LaneReward) -> dict[str, Any]:
    return {
        "project_id": payload.project_id,
        "lane_id": payload.lane_id,
        "readiness": payload.readiness,
        "ci_passed": payload.ci_passed,
        "review_passed": payload.review_passed,
        "human_merged": payload.human_merged,
        "blocked_reasons": list(payload.blocked_reasons),
        "prompt_patterns": list(payload.prompt_patterns),
        "created_at": payload.created_at,
    }


def record_lane_reward(
    project_id: str,
    lane_id: str,
    readiness: str,
    *,
    ci_passed: bool,
    review_passed: bool,
    human_merged: bool = False,
    blocked_reasons: tuple[str, ...] | None = None,
    prompt_patterns: tuple[str, ...] | None = None,
    control_root: Path | None = None,
) -> Path:
    entry = LaneReward(
        project_id=project_id,
        lane_id=lane_id,
        readiness=readiness,
        ci_passed=ci_passed,
        review_passed=review_passed,
        human_merged=human_merged,
        blocked_reasons=blocked_reasons or (),
        prompt_patterns=prompt_patterns or (),
        created_at=now_iso(),
    )
    path = _rewards_path(project_id, control_root=control_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_as_entry(entry), sort_keys=True) + "\n")

    if readiness in {"blocked", "not_ready", "draft_ready"} and blocked_reasons:
        for reason in blocked_reasons:
            append_failure_pattern(project_id, reason, control_root=control_root)
    return path


def list_lane_rewards(
    project_id: str,
    *,
    control_root: Path | None = None,
) -> list[LaneReward]:
    path = _rewards_path(project_id, control_root=control_root)
    if not path.is_file():
        return []

    rewards: list[LaneReward] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        rewards.append(
            LaneReward(
                project_id=str(payload.get("project_id", "")),
                lane_id=str(payload.get("lane_id", "")),
                readiness=str(payload.get("readiness", "")),
                ci_passed=bool(payload.get("ci_passed", False)),
                review_passed=bool(payload.get("review_passed", False)),
                human_merged=bool(payload.get("human_merged", False)),
                blocked_reasons=tuple(str(item) for item in payload.get("blocked_reasons", [])),
                prompt_patterns=tuple(str(item) for item in payload.get("prompt_patterns", [])),
                created_at=str(payload.get("created_at", "")),
            )
        )
    return rewards


def summarize_successful_prompt_patterns(
    project_id: str,
    *,
    control_root: Path | None = None,
    max_items: int = 50,
) -> tuple[str, ...]:
    rewards = list_lane_rewards(project_id, control_root=control_root)
    successes = [entry for entry in rewards if entry.readiness == "ready" and entry.ci_passed and entry.review_passed]
    patterns: list[str] = []
    for entry in successes:
        patterns.extend(entry.prompt_patterns)
    return tuple(patterns[-max_items:])


def write_reward_summary(project_id: str, *, control_root: Path | None = None) -> Path:
    rewards = list_lane_rewards(project_id, control_root=control_root)
    path = _memory_dir(project_id, control_root=control_root) / "reward-summary.json"
    summary = {
        "project_id": project_id,
        "reward_events": len(rewards),
        "successful_prompts": summarize_successful_prompt_patterns(project_id, control_root=control_root),
        "updated_at": now_iso(),
    }
    write_json_atomic(path, summary)
    return path
