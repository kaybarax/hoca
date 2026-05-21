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
    assert "scripts/check-definition-of-ready.sh" in content
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


def test_manager_skill_documents_optional_kanban_orchestration() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    assert "## Kanban orchestration (optional)" in content
    assert "### Kanban task contract" in content
    assert "HOCA_USE_KANBAN" in content
    assert "board: hoca:<repo-slug>" in content
    assert "hoca-manager" in content
    assert "hoca-worker" in content
    assert "hoca-reviewer" in content
    assert "implement r<N>" in content
    assert "review r<N>" in content
    assert "repair r<N>" in content
    for status in ("triage", "todo", "ready", "running", "blocked", "done"):
        assert status in content
    for prefix in ("[spec]", "[artifact]", "[validation]", "[decision]", "[round]"):
        assert prefix in content
    assert "current_round" in content
    assert "max_total_rounds" in content
    assert "kanban_complete" in content
    assert "kanban_create" in content
    assert "kanban_link" in content
    lowered = content.lower()
    assert "do not require kanban" in lowered or "without creating or updating kanban tasks" in lowered


def test_manager_skill_defines_kanban_task_contract() -> None:
    content = (SKILLS_DIR / "hoca-manager.md").read_text(encoding="utf-8")
    required = (
        "The **parent task body** must include:",
        "The **worker child task body** must include:",
        "The **reviewer child task body** must include:",
        "The **repair child task body** is a worker child with a narrower contract:",
        "Run artifact links",
        "attempts/worker-attempt-<round>.json",
        "reviews/review-report-<round>.json",
        "validation/validation-report-<round>.json",
        "decisions/manager-decision-<round>.json",
        "final-state.json",
        "Use structured run artifacts and Kanban comments as the shared context",
        "Do not require or assume direct shared memory",
    )
    for expected in required:
        assert expected in content


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


def test_reviewer_skill_is_review_focused() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
    assert "# HOCA Reviewer (QA)" in content
    assert "hoca-manager.md" in content
    assert "hoca-worker-openhands.md" in content
    assert "hoca-sandbox-policy.md" in content
    assert "scripts/review-with-openhands.sh" in content
    lowered = content.lower()
    assert "review-only" in lowered
    assert "never invoke openhands directly" in lowered


def test_reviewer_skill_defines_manual_procedures() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
    required_sections = (
        "### 1. Receive task spec and diff context",
        "### 2. Read project instructions",
        "### 3. Write the OpenHands review prompt",
        "### 4. Call the wrapper script",
        "### 5. Classify findings",
        "### 6. Produce `HocaReviewReport`",
        "### 7. PR notes and tech debt",
    )
    for section in required_sections:
        assert section in content


def test_reviewer_skill_defines_categories_and_severities() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
    for category in (
        "correctness",
        "security",
        "test",
        "scope",
        "maintainability",
        "style",
        "tooling",
        "environment",
    ):
        assert category in content
    for severity in ("critical", "high", "medium", "low", "nit"):
        assert severity in content
    assert "## Review categories" in content
    assert "## Severity meanings" in content


def test_reviewer_skill_defines_verdict_conditions() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
    assert "#### `LGTM` conditions" in content
    assert "#### `fix_required` conditions" in content
    assert "#### `blocked` conditions" in content
    lowered = content.lower()
    assert "do not block on pure preference" in lowered


def test_reviewer_skill_references_structured_artifacts() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
    assert "HocaTaskSpec" in content
    assert "HocaAttemptReport" in content
    assert "HocaReviewReport" in content
    assert "task-spec.json" in content
    assert "review-report-<round>.json" in content
    assert "templates/HocaReviewReport.yaml" in content


def test_reviewer_skill_maps_output_to_review_report() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
    for field in ("verdict", "findings", "pr_notes", "required_fix", "severity", "category"):
        assert field in content
    assert "role" in content and "reviewer" in content
    assert "known_followups" in content


def test_reviewer_skill_never_owns_git_lifecycle() -> None:
    content = (SKILLS_DIR / "hoca-reviewer-qa.md").read_text(encoding="utf-8")
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


def test_pr_publisher_defines_manual_procedures() -> None:
    content = (SKILLS_DIR / "hoca-pr-publisher.md").read_text(encoding="utf-8")
    required_sections = (
        "### 1. Confirm publication decision",
        "### 2. Build intended-file list",
        "### 3. Safe stage",
        "### 4. Commit",
        "### 5. Create pull request",
        "### 6. Merge policy",
        "### 7. Cleanup and branch restoration",
    )
    for section in required_sections:
        assert section in content


def test_pr_publisher_defines_safe_staging_prerequisites() -> None:
    content = (SKILLS_DIR / "hoca-pr-publisher.md").read_text(encoding="utf-8")
    assert "## Prerequisites (safe staging)" in content
    assert "intended-files.txt" in content
    assert "intended-files-source.txt" in content
    assert "git add ." in content
    assert "hoca.review_gate" in content
    assert "require_tests=true" in content
    assert "require_review_lgtm=true" in content


def test_pr_publisher_defines_pr_body_requirements() -> None:
    content = (SKILLS_DIR / "hoca-pr-publisher.md").read_text(encoding="utf-8")
    assert "## PR body requirements" in content
    for section in ("Summary", "Changes", "Validation", "Code Review", "Risk", "Linked Issue"):
        assert section in content
    assert "tests-summary.md" in content
    assert "known_followups" in content or "tech debt" in content.lower()


def test_pr_publisher_defines_token_handling() -> None:
    content = (SKILLS_DIR / "hoca-pr-publisher.md").read_text(encoding="utf-8")
    assert "## Token handling" in content
    assert "GITHUB_TOKEN" in content
    lowered = content.lower()
    assert "never include tokens" in lowered or "must not receive" in lowered


def test_pr_publisher_defines_cleanup_and_branch_restoration() -> None:
    content = (SKILLS_DIR / "hoca-pr-publisher.md").read_text(encoding="utf-8")
    assert "branch restoration" in content.lower()
    assert "HOCA_DEV_BRANCH" in content
    assert "record-final" in content
    assert "HOCA_KEEP_RUNTIME" in content


def test_sandbox_policy_documents_defaults() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Sandbox defaults" in content
    assert "HOCA_USE_SANDBOX" in content
    assert "sandbox-policy.json" in content
    assert "templates/HocaSandboxPolicy.yaml" in content
    assert "run-openhands-sandboxed.sh" in content


def test_sandbox_policy_documents_network_modes() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Network modes" in content
    assert "HOCA_NETWORK_MODE" in content
    assert "## Docker implementation" in content
    for mode in ("offline", "package-install", "github-only", "full"):
        assert mode in content


def test_sandbox_policy_documents_forbidden_mounts() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Forbidden mounts and access" in content
    lowered = content.lower()
    assert "docker socket" in lowered
    assert "ssh" in lowered or "gpg" in lowered
    assert "forbidden" in lowered


def test_sandbox_policy_documents_credential_isolation() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Credential isolation" in content
    assert "GITHUB_TOKEN" in content
    lowered = content.lower()
    assert "never forward" in lowered or "must not receive" in lowered
    assert "LLM_API_KEY" in content


def test_sandbox_policy_documents_host_execution() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Host execution" in content
    assert "HOCA_USE_SANDBOX=false" in content
    assert "run-openhands-task.sh" in content
    assert "host-execution-warning.txt" in content


def test_sandbox_policy_documents_nested_sandboxes() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Nested sandboxes and Hermes-in-Docker" in content
    assert "one explicit HOCA-controlled" in content
    assert "task worktree" in content


def test_sandbox_policy_documents_unsafe_activity_stop() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Stop on unsafe activity" in content
    assert "monitor-stop.json" in content
    lowered = content.lower()
    assert "blocked" in lowered
    assert "do not stage" in lowered or "do not stage, commit" in lowered


def test_sandbox_policy_links_role_skills() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Related skills" in content
    for skill in (
        "hoca-manager.md",
        "hoca-worker-openhands.md",
        "hoca-reviewer-qa.md",
        "hoca-pr-publisher.md",
    ):
        assert skill in content


def test_sandbox_policy_aligns_with_scripts() -> None:
    content = (SKILLS_DIR / "hoca-sandbox-policy.md").read_text(encoding="utf-8")
    assert "## Alignment with scripts" in content
    assert "sandbox-manager.sh" in content
    assert "HocaSandboxPolicy" in content


def test_sandbox_scripts_do_not_forward_github_token() -> None:
    for script_name in ("run-openhands-sandboxed.sh", "sandbox-manager.sh"):
        script = REPO_ROOT / "scripts" / script_name
        content = script.read_text(encoding="utf-8")
        assert "GITHUB_TOKEN" not in content, f"{script_name} must not forward GITHUB_TOKEN"


def test_sandbox_scripts_drop_capabilities_without_net_raw() -> None:
    for script_name in ("run-openhands-sandboxed.sh", "sandbox-manager.sh"):
        script = REPO_ROOT / "scripts" / script_name
        content = script.read_text(encoding="utf-8")
        assert "--cap-drop=ALL" in content, f"{script_name} must drop all capabilities"
        assert "NET_RAW" not in content, f"{script_name} must not grant NET_RAW"
        assert "--cap-add=" not in content, f"{script_name} must not add Linux capabilities"


def test_sandbox_scripts_run_as_non_root() -> None:
    for script_name in ("run-openhands-sandboxed.sh", "sandbox-manager.sh"):
        script = REPO_ROOT / "scripts" / script_name
        content = script.read_text(encoding="utf-8")
        assert "--user root" not in content, f"{script_name} must not run as root"
        assert 'sandbox_resolve_user' in content, f"{script_name} must resolve a non-root user"
        assert "--user" in content, f"{script_name} must pass --user to docker run"


def test_sandbox_scripts_apply_network_modes() -> None:
    for script_name in ("run-openhands-sandboxed.sh", "sandbox-manager.sh"):
        script = REPO_ROOT / "scripts" / script_name
        content = script.read_text(encoding="utf-8")
        assert "sandbox_resolve_network_mode" in content, script_name
        assert "sandbox_docker_network_args" in content, script_name
    sandboxed = (REPO_ROOT / "scripts" / "run-openhands-sandboxed.sh").read_text(encoding="utf-8")
    assert "sandbox_record_network_policy" in sandboxed


def test_sandbox_scripts_avoid_runtime_root_package_install() -> None:
    sandboxed = (REPO_ROOT / "scripts" / "run-openhands-sandboxed.sh").read_text(encoding="utf-8")
    assert "apt-get" not in sandboxed
    assert "pip install" not in sandboxed


def test_sandbox_dockerfile_defines_worker_and_openhands() -> None:
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile.sandbox").read_text(encoding="utf-8")
    assert "useradd" in dockerfile and "worker" in dockerfile
    assert "USER worker" in dockerfile
    assert "python3-venv" in dockerfile
    assert "UV_TOOL_DIR" in dockerfile
    assert "uv tool install openhands --python 3.12 --with openhands-ai" in dockerfile
    assert "openhands-ai" in dockerfile
    assert "openhands --help" in dockerfile
    assert "apt-get" not in dockerfile.split("USER worker", 1)[-1]
