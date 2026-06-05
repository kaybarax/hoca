from __future__ import annotations

from hoca.context_pack import load_project_context_pack
from hoca.project_memory import list_lane_rewards, record_lane_reward, summarize_successful_prompt_patterns


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
