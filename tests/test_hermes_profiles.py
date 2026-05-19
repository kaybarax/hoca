from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "hermes-profiles"

PROFILE_NAMES = ("hoca-manager", "hoca-worker", "hoca-reviewer")

PROFILE_FILES = ("SOUL.md", "config.example.yaml", "README.md")

SOUL_REQUIRED_SECTIONS = (
    "## Identity",
    "## Must never",
    "## Escalate",
)

FORBIDDEN_SECRET_FRAGMENTS = (
    "/users/",
    "/home/",
    "api_key:",
    "sk-",
    "ghp_",
    "github_pat_",
    "id_rsa",
    "credentials.json",
)


def _strip_yaml_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(lines)


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_profile_directory_exists(profile_name: str) -> None:
    path = PROFILES_DIR / profile_name
    assert path.is_dir(), f"Missing profile template directory: {path}"


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
@pytest.mark.parametrize("filename", PROFILE_FILES)
def test_profile_template_files_exist(profile_name: str, filename: str) -> None:
    path = PROFILES_DIR / profile_name / filename
    assert path.is_file(), f"Missing {filename} for profile {profile_name}: {path}"


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_profile_soul_documents_role_identity(profile_name: str) -> None:
    content = (PROFILES_DIR / profile_name / "SOUL.md").read_text(encoding="utf-8")
    assert profile_name in content, f"{profile_name}/SOUL.md should name the profile"
    for section in SOUL_REQUIRED_SECTIONS:
        assert section in content, (
            f"{profile_name}/SOUL.md is missing section {section!r}"
        )


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_profile_config_declares_hoca_role(profile_name: str) -> None:
    content = (PROFILES_DIR / profile_name / "config.example.yaml").read_text(
        encoding="utf-8"
    )
    body = _strip_yaml_comments(content).lower()
    expected_role = profile_name.removeprefix("hoca-")
    assert re.search(rf"^\s*role:\s*{re.escape(expected_role)}\s*$", body, re.MULTILINE), (
        f"{profile_name}/config.example.yaml must declare hoca.role: {expected_role}"
    )


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_profile_templates_avoid_secret_like_content(profile_name: str) -> None:
    for filename in ("SOUL.md", "config.example.yaml", "README.md"):
        content = (PROFILES_DIR / profile_name / filename).read_text(
            encoding="utf-8"
        ).lower()
        for fragment in FORBIDDEN_SECRET_FRAGMENTS:
            assert fragment not in content, (
                f"{profile_name}/{filename} must not include secret-like content: "
                f"{fragment!r}"
            )


def test_worker_profile_omits_pr_creator() -> None:
    content = (
        PROFILES_DIR / "hoca-worker" / "config.example.yaml"
    ).read_text(encoding="utf-8").lower()
    assert "pr_creator" not in content


def test_reviewer_profile_omits_openhands_runner() -> None:
    content = (
        PROFILES_DIR / "hoca-reviewer" / "config.example.yaml"
    ).read_text(encoding="utf-8").lower()
    assert "openhands_runner" not in content


def test_manager_profile_includes_orchestration_scripts() -> None:
    content = (
        PROFILES_DIR / "hoca-manager" / "config.example.yaml"
    ).read_text(encoding="utf-8").lower()
    for script_key in ("pr_creator", "task_runner", "code_reviewer"):
        assert script_key in content, (
            f"hoca-manager config should reference {script_key}"
        )


MANAGER_SOUL_REQUIRED_SECTIONS = (
    "## Arbitration rule",
    "## Hard limits",
    "## Failure behavior",
)

MANAGER_SOUL_REQUIRED_PHRASES = (
    "engineering manager",
    "team lead",
    "product-owner delegate",
    "task clarity",
    "safety policy",
    "quality signals, not commands",
    "final authority",
    "max_total_rounds",
)


def test_manager_soul_documents_manager_role_contract() -> None:
    content = (PROFILES_DIR / "hoca-manager" / "SOUL.md").read_text(encoding="utf-8")
    lowered = content.lower()
    for section in MANAGER_SOUL_REQUIRED_SECTIONS:
        assert section in content, f"hoca-manager/SOUL.md is missing section {section!r}"
    for phrase in MANAGER_SOUL_REQUIRED_PHRASES:
        assert phrase in lowered, (
            f"hoca-manager/SOUL.md should mention {phrase!r}"
        )
    assert "git lifecycle" in lowered
    assert "hoca-worker" in content
    assert "hoca-reviewer" in content


WORKER_SOUL_REQUIRED_SECTIONS = (
    "## Implementation discipline",
    "## Attempt report obligations",
    "## Repair mode",
    "## Hard limits",
)

WORKER_SOUL_REQUIRED_PHRASES = (
    "principal full-stack",
    "minimal-change",
    "hocataskspec",
    "implementation quality",
    "hocaattemptreport",
    "stage, commit, push, merge",
    "secret",
    "repair brief",
    "accepted findings",
)


def test_worker_soul_documents_worker_role_contract() -> None:
    content = (PROFILES_DIR / "hoca-worker" / "SOUL.md").read_text(encoding="utf-8")
    lowered = content.lower()
    for section in WORKER_SOUL_REQUIRED_SECTIONS:
        assert section in content, f"hoca-worker/SOUL.md is missing section {section!r}"
    for phrase in WORKER_SOUL_REQUIRED_PHRASES:
        assert phrase in lowered, (
            f"hoca-worker/SOUL.md should mention {phrase!r}"
        )
    assert "pull request" in lowered or "pr " in lowered
    assert "hoca-manager" in content


def test_profiles_readme_lists_all_profiles() -> None:
    content = (PROFILES_DIR / "README.md").read_text(encoding="utf-8")
    for profile_name in PROFILE_NAMES:
        assert profile_name in content
