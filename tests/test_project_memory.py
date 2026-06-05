from __future__ import annotations

import json
from pathlib import Path

from hoca.context_pack import load_project_context_pack
from hoca.project_memory import (
    infer_lane_reward_from_run_dir,
    list_lane_rewards,
    record_lane_reward,
    record_lane_reward_from_run_dir,
    summarize_successful_prompt_patterns,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_run_status(run_dir: Path, *, payload: dict[str, object]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "status.json", payload)


def _write_validation_report(
    run_dir: Path,
    *,
    tests_passed: bool = True,
    secret_scan_clean: bool = True,
    monitor_clean: bool = True,
    hard_blockers: list[str] | None = None,
) -> None:
    hard_blockers = hard_blockers or []
    _write_json(
        run_dir / "validation" / "validation-report-1.json",
        {
            "run_id": "run-1",
            "round": 1,
            "tests_passed": tests_passed,
            "test_failure_type": None,
            "git_status": [],
            "changed_files": [],
            "secret_scan_clean": secret_scan_clean,
            "monitor_clean": monitor_clean,
            "monitor_stop_reason": None,
            "hard_blockers": hard_blockers,
            "scope_risk": False,
            "staging_risk": False,
            "artifact_paths": {},
        },
    )


def _write_review_report(
    run_dir: Path,
    *,
    verdict: str = "pass",
) -> None:
    _write_json(
        run_dir / "reviews" / "review-report-1.json",
        {
            "run_id": "run-1",
            "round": 1,
            "role": "reviewer",
            "verdict": verdict,
            "findings": [],
            "pr_notes": {"summary": []},
        },
    )


def test_record_lane_reward_round_trips_jsonl(tmp_path) -> None:
    record_lane_reward(
        "project-memory",
        "lane-1",
        "ready",
        ci_passed=True,
        review_passed=True,
        human_merged=False,
        prompt_patterns=("prompt-a", "prompt-b"),
        control_root=tmp_path,
    )

    rewards = list_lane_rewards("project-memory", control_root=tmp_path)
    assert len(rewards) == 1
    reward = rewards[0]
    assert reward.readiness == "ready"
    assert reward.ci_passed is True
    assert reward.prompt_patterns == ("prompt-a", "prompt-b")


def test_blocked_readiness_appends_failure_patterns(tmp_path) -> None:
    record_lane_reward(
        "project-memory",
        "lane-2",
        "blocked",
        ci_passed=False,
        review_passed=False,
        blocked_reasons=("timeout", "flaky-test"),
        control_root=tmp_path,
    )

    pack = load_project_context_pack("project-memory", control_root=tmp_path)
    assert pack.failure_patterns == ("timeout", "flaky-test")


def test_summarize_successful_prompt_patterns_filters_readiness(tmp_path) -> None:
    record_lane_reward(
        "project-memory",
        "lane-3",
        "ready",
        ci_passed=True,
        review_passed=True,
        prompt_patterns=("good-a",),
        control_root=tmp_path,
    )
    record_lane_reward(
        "project-memory",
        "lane-4",
        "not_ready",
        ci_passed=True,
        review_passed=True,
        prompt_patterns=("ignored",),
        control_root=tmp_path,
    )
    record_lane_reward(
        "project-memory",
        "lane-5",
        "ready",
        ci_passed=True,
        review_passed=False,
        prompt_patterns=("ignored-two",),
        control_root=tmp_path,
    )

    assert summarize_successful_prompt_patterns("project-memory", control_root=tmp_path) == (
        "good-a",
    )


def test_infer_lane_reward_from_run_dir_captures_ci_review_and_prompts(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_dir / "status.json",
        {
            "status": "running",
            "prompt_patterns": ["status-pattern"],
        },
    )
    _write_validation_report(run_dir)
    _write_review_report(run_dir)
    _write_json(run_dir / "attempts" / "worker-attempt-1.json", {})

    (run_dir / "worker-hermes-prompt-round-1.txt").write_text(
        "first worker prompt\n", encoding="utf-8"
    )
    (run_dir / "reviewer-hermes-prompt-round-1.txt").write_text(
        "second reviewer prompt\n", encoding="utf-8"
    )

    reward = infer_lane_reward_from_run_dir(
        "project-memory",
        "lane-1",
        run_dir,
        readiness="ready",
        control_root=tmp_path,
    )

    assert reward.readiness == "ready"
    assert reward.ci_passed is True
    assert reward.review_passed is True
    assert reward.human_merged is False
    assert reward.blocked_reasons == ()
    assert reward.prompt_patterns == (
        "worker:first worker prompt",
        "reviewer:second reviewer prompt",
        "status-pattern",
    )


def test_infer_lane_reward_from_run_dir_flags_blocked_reasons(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_run_status(
        run_dir,
        payload={
            "status": "failed",
            "reason": "tests failed",
        },
    )
    _write_validation_report(
        run_dir,
        tests_passed=False,
        secret_scan_clean=False,
        hard_blockers=["scope_risk"],
    )
    _write_review_report(run_dir, verdict="fix_required")

    reward = infer_lane_reward_from_run_dir(
        "project-memory",
        "lane-2",
        run_dir,
        readiness="blocked",
        control_root=tmp_path,
    )

    assert reward.readiness == "blocked"
    assert reward.ci_passed is False
    assert reward.review_passed is False
    assert reward.human_merged is False
    assert "validation:tests_failed" in reward.blocked_reasons
    assert "validation:secret_scan_detected" in reward.blocked_reasons
    assert "validation:scope_risk" in reward.blocked_reasons
    assert "review:fix_required" in reward.blocked_reasons
    assert "run.reason:tests failed" in reward.blocked_reasons
    assert "run.status:failed" in reward.blocked_reasons


def test_record_lane_reward_from_run_dir_appends_failure_patterns(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(run_dir / "status.json", {"status": "blocked", "reason": "lane blocked by policy"})
    _write_json(
        run_dir / "validation" / "validation-report-1.json", {"run_id": "run-1", "round": 1}
    )

    path = record_lane_reward_from_run_dir(
        "project-memory",
        "lane-3",
        run_dir,
        readiness="blocked",
        control_root=tmp_path,
    )

    rewards = list_lane_rewards("project-memory", control_root=tmp_path)
    assert path.is_file()
    assert len(rewards) == 1
    assert rewards[0].blocked_reasons

    pack = load_project_context_pack("project-memory", control_root=tmp_path)
    assert any(
        pattern.startswith("run.reason:lane blocked by policy") for pattern in pack.failure_patterns
    )
    assert any(pattern.startswith("validation:tests_failed") for pattern in pack.failure_patterns)
    assert any(pattern.startswith("run.status:blocked") for pattern in pack.failure_patterns)


def test_infer_lane_reward_marks_human_merged_when_final_state_shows_pr_opened(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_dir / "status.json",
        {
            "status": "ready_for_human",
            "prompt_patterns": [],
        },
    )
    _write_validation_report(run_dir)
    _write_review_report(run_dir)
    _write_json(run_dir / "final-state.json", {"status": "pr_opened", "reason": "ready"})

    reward = infer_lane_reward_from_run_dir(
        "project-memory",
        "lane-4",
        run_dir,
        readiness="ready",
        control_root=tmp_path,
    )

    assert reward.human_merged is True
