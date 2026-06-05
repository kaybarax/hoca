from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.context_pack import append_failure_pattern
from hoca.control_paths import make_fleet_control_paths
from hoca.run_state import now_iso, write_json_atomic


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
