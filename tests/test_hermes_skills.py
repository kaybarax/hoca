from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "hermes-skills"

COMPAT_ENTRYPOINT = "hoca.md"

ROLE_SKILL_FILES = (
    "hoca-manager.md",
    "hoca-worker-openhands.md",
    "hoca-reviewer-qa.md",
    "hoca-pr-publisher.md",
    "hoca-sandbox-policy.md",
)

ALL_SKILL_FILES = (COMPAT_ENTRYPOINT, *ROLE_SKILL_FILES)

MANAGER_ONLY_GIT_PATTERNS = (
    r"scripts/safe-stage-after-review\.sh",
    r"scripts/commit-after-staging\.sh",
    r"scripts/create-pr\.sh",
    r"git add \.",
    r"git add -A",
    r"git commit -am",
)

WORKER_REVIEWER_FORBIDDEN_PATTERNS = MANAGER_ONLY_GIT_PATTERNS + (
    r"scripts/run-hoca-task\.sh",
)


@pytest.mark.parametrize("filename", ALL_SKILL_FILES)
def test_hermes_skill_file_exists(filename: str) -> None:
    path = SKILLS_DIR / filename
    assert path.is_file(), f"Missing Hermes skill: {path}"


def test_compat_entrypoint_keeps_openhands_boss_title() -> None:
    content = (SKILLS_DIR / COMPAT_ENTRYPOINT).read_text(encoding="utf-8")
    assert "# Hoca OpenHands Boss" in content
    assert "use Hoca OpenHands Boss" in content
    assert "scripts/run-hoca-task.sh" in content


def test_compat_entrypoint_links_role_skills() -> None:
    content = (SKILLS_DIR / COMPAT_ENTRYPOINT).read_text(encoding="utf-8")
    for filename in ROLE_SKILL_FILES:
        assert filename in content


def test_manager_skill_is_orchestration_focused() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    assert "# HOCA Manager" in content
    assert "hoca-worker-openhands.md" in content
    assert "hoca-reviewer-qa.md" in content
    assert "hoca-pr-publisher.md" in content
    assert "scripts/run-openhands-task.sh" in content
    assert "scripts/review-with-openhands.sh" in content


def test_manager_skill_defines_manual_procedures() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    required_sections = (
        "### 1. Intake",
        "### 2. Definition of ready",
        "### 3. Task spec output",
        "### 5. Worker assignment",
        "### 6. Deterministic validation",
        "### 7. Reviewer assignment",
        "### 8. Manager arbitration",
        "### 9. Repair loop and max rounds",
        "### 10. PR and cleanup",
        "### 11. Human escalation triggers",
    )
    for section in required_sections:
        assert section in content


def test_manager_skill_references_structured_artifacts() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    assert "HocaTaskSpec" in content
    assert "HocaAttemptReport" in content
    assert "HocaValidationReport" in content
    assert "HocaReviewReport" in content
    assert "HocaManagerDecision" in content
    assert "task-spec.json" in content


def test_manager_skill_uses_wrapper_scripts_not_raw_openhands() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    assert "scripts/run-openhands-task.sh" in content
    assert "scripts/review-with-openhands.sh" in content
    assert "scripts/hoca-doctor.sh" in content
    assert "scripts/run-tests.sh" in content
    lowered = content.lower()
    assert "never call openhands directly" in lowered


def test_manager_skill_forbids_bypassing_safety_gates() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    lowered = content.lower()
    assert "safety gates" in lowered
    assert "never bypass" in lowered
    assert "require_tests=true" in content
    assert "require_review_lgtm=true" in content
    assert "max_total_rounds" in content


def test_manager_skill_distinguishes_trivial_edits_from_worker_implementation() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    lowered = content.lower()
    assert "trivial mechanical edits" in lowered
    assert "route all non-trivial implementation through the worker" in lowered


def test_worker_skill_is_implementation_focused() -> None:
    content = (SKILLS_DIR / "hoca-worker-openhands.md").read_text(encoding="utf-8")
    assert "# HOCA Worker (OpenHands)" in content
    assert "hoca-manager.md" in content
    assert "hoca-sandbox-policy.md" in content
    assert "scripts/run-openhands-task.sh" in content
    lowered = content.lower()
    assert "implementation-only" in lowered
    assert "never invoke openhands directly" in lowered


def test_worker_skill_defines_manual_procedures() -> None:
    content = (SKILLS_DIR / "hoca-worker-openhands.md").read_text(encoding="utf-8")
    required_sections = (
        "### 1. Receive `HocaTaskSpec`",
        "### 2. Read project instructions",
        "### 3. Write the OpenHands implementation prompt",
        "### 4. Call the wrapper script",
        "### 5. Summarize changes (`HocaAttemptReport`)",
        "### 6. Repair prompts",
    )
    for section in required_sections:
        assert section in content


def test_worker_skill_references_structured_artifacts() -> None:
    content = (SKILLS_DIR / "hoca-worker-openhands.md").read_text(encoding="utf-8")
    assert "HocaTaskSpec" in content
    assert "HocaAttemptReport" in content
    assert "task-spec.json" in content
    assert "worker-attempt-<round>.json" in content
    assert "record-worker" in content


def test_worker_skill_maps_output_to_attempt_report() -> None:
    content = (SKILLS_DIR / "hoca-worker-openhands.md").read_text(encoding="utf-8")
    for field in (
        "changed_files",
        "summary",
        "commands_run",
        "tests_run",
        "known_risks",
        "blocked_reason",
        "artifact_paths",
    ):
        assert field in content
    assert "`status`" in content or "status" in content
    assert "role" in content and "worker" in content


def test_worker_skill_defines_repair_handling() -> None:
    content = (SKILLS_DIR / "hoca-worker-openhands.md").read_text(encoding="utf-8")
    lowered = content.lower()
    assert "repair" in lowered
    assert "next_worker_brief" in content
    assert "accepted" in lowered
    assert "rejected" in lowered


def test_worker_skill_never_owns_git_lifecycle() -> None:
    content = (SKILLS_DIR / "hoca-worker-openhands.md").read_text(encoding="utf-8")
    lowered = content.lower()
    assert "must never" in lowered
    assert "git lifecycle" in lowered
    assert "hoca-pr-publisher.md" in content


@pytest.mark.parametrize("filename", ("hoca-worker-openhands.md", "hoca-reviewer-qa.md"))
def test_worker_and_reviewer_skills_omit_manager_git_powers(filename: str) -> None:
    content = (SKILLS_DIR / filename).read_text(encoding="utf-8")
    lowered = content.lower()
    for pattern in WORKER_REVIEWER_FORBIDDEN_PATTERNS:
        assert re.search(pattern, content) is None, (
            f"{filename} must not document manager-only Git command {pattern!r}"
        )
    assert "must never" in lowered
    assert "git lifecycle" in lowered or "git add" in lowered


def test_pr_publisher_is_manager_only() -> None:
    content = (SKILLS_DIR / "hoca-pr-publisher.md").read_text(encoding="utf-8")
    assert "Manager-only" in content or "manager-only" in content
    assert "scripts/safe-stage-after-review.sh" in content
    assert "scripts/create-pr.sh" in content
    assert "hoca-worker" in content
    assert "hoca-reviewer" in content


def test_sandbox_policy_documents_isolation() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "HOCA_USE_SANDBOX" in content or "sandbox" in content.lower()
    assert "run-openhands-sandboxed.sh" in content
    assert "GITHUB_TOKEN" in content
    assert "forbidden" in content.lower() or "Forbidden" in content
