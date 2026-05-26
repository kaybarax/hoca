from __future__ import annotations

import pytest

from hoca.config import (
    DEFAULT_POLICY,
    HocaConfig,
    PolicyError,
    assert_review_approved,
    assert_commit_allowed,
    assert_tests_passed,
    validate_run_options,
)


def test_required_defaults_are_safe() -> None:
    cfg = HocaConfig()
    assert cfg.use_kanban is False
    assert cfg.use_sandbox is True
    assert cfg.use_worktree_sandbox is True
    assert cfg.network_mode == "offline"
    assert cfg.max_total_rounds == 3
    assert cfg.require_review is True
    assert cfg.model_pool.is_active is False
    assert DEFAULT_POLICY.auto_merge is False
    assert DEFAULT_POLICY.require_pull_request is True
    assert DEFAULT_POLICY.forbid_direct_push_to_main is True
    assert DEFAULT_POLICY.require_clean_working_tree is True
    assert DEFAULT_POLICY.stop_on_unrelated_changes is True
    assert DEFAULT_POLICY.stop_on_secret_changes is True
    assert DEFAULT_POLICY.stop_on_test_failure is True
    assert DEFAULT_POLICY.require_review_approval is True
    assert DEFAULT_POLICY.stop_before_commit_until_selective_staging is True
    assert DEFAULT_POLICY.allow_high_risk_auto_merge is False


def test_auto_merge_and_direct_main_push_are_rejected_by_default() -> None:
    with pytest.raises(PolicyError, match="Auto-merge"):
        validate_run_options(auto_merge=True)

    with pytest.raises(PolicyError, match="Direct pushes"):
        validate_run_options(direct_main_push=True)

    with pytest.raises(PolicyError, match="High-risk"):
        validate_run_options(auto_merge=True, high_risk=True)


def test_failed_tests_and_unapproved_reviews_stop_runs() -> None:
    with pytest.raises(PolicyError, match="Tests failed"):
        assert_tests_passed(1)

    with pytest.raises(PolicyError, match="Code review"):
        assert_review_approved("needs changes")

    assert_review_approved("approved")


def test_commit_stops_until_selective_staging_is_ready() -> None:
    with pytest.raises(PolicyError, match="Selective staging"):
        assert_commit_allowed(selective_staging_ready=False)
