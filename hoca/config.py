from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyPolicy:
    auto_merge: bool = False
    require_pull_request: bool = True
    forbid_direct_push_to_main: bool = True
    require_clean_working_tree: bool = True
    stop_on_unrelated_changes: bool = True
    stop_on_secret_changes: bool = True
    stop_on_test_failure: bool = True
    require_aider_approval: bool = True
    stop_before_commit_until_selective_staging: bool = True
    allow_high_risk_auto_merge: bool = False


DEFAULT_POLICY = SafetyPolicy()


class PolicyError(RuntimeError):
    """Raised when requested behavior violates HOCA's default safety policy."""


def validate_run_options(
    *,
    auto_merge: bool = False,
    high_risk: bool = False,
    direct_main_push: bool = False,
    policy: SafetyPolicy = DEFAULT_POLICY,
) -> None:
    if high_risk and auto_merge and not policy.allow_high_risk_auto_merge:
        raise PolicyError("High-risk changes must never be auto-merged.")

    if auto_merge and not policy.auto_merge:
        raise PolicyError("Auto-merge is disabled by default.")

    if direct_main_push and policy.forbid_direct_push_to_main:
        raise PolicyError("Direct pushes to main are forbidden by default.")


def assert_tests_passed(returncode: int, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
    if returncode != 0 and policy.stop_on_test_failure:
        raise PolicyError("Tests failed; stopping the run.")


def assert_aider_approved(review_text: str, *, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
    normalized = review_text.strip().lower()
    approved = normalized in {"approved", "approval: approved", "hoca-review: approved"}
    if policy.require_aider_approval and not approved:
        raise PolicyError("Aider review did not return approval; stopping the run.")


def assert_commit_allowed(*, selective_staging_ready: bool, policy: SafetyPolicy = DEFAULT_POLICY) -> None:
    if policy.stop_before_commit_until_selective_staging and not selective_staging_ready:
        raise PolicyError("Selective staging is not fully implemented; stopping before commit.")
