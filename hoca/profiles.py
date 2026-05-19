from __future__ import annotations

import os
import shutil
from pathlib import Path

from hoca.paths import repo_root
from hoca.subprocess_utils import run_command

PROFILE_MANAGER = "hoca-manager"
PROFILE_WORKER = "hoca-worker"
PROFILE_REVIEWER = "hoca-reviewer"

PROFILE_NAMES: tuple[str, ...] = (PROFILE_MANAGER, PROFILE_WORKER, PROFILE_REVIEWER)

PROFILE_TEMPLATE_FILES: tuple[str, ...] = ("SOUL.md", "config.example.yaml", "README.md")

COMPAT_SKILL_FILENAME = "hoca.md"
ROLE_SKILL_FILENAMES: tuple[str, ...] = (
    "hoca-manager.md",
    "hoca-worker-openhands.md",
    "hoca-reviewer-qa.md",
    "hoca-pr-publisher.md",
    "hoca-sandbox-policy.md",
)
HERMES_SKILL_FILENAMES: tuple[str, ...] = (COMPAT_SKILL_FILENAME, *ROLE_SKILL_FILENAMES)

_SETUP_SCRIPT_NAME = "setup-hermes-profiles.sh"
_PROFILE_HELP_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("profile", "list"),
    ("profile", "create"),
    ("profile", "show"),
)


def profiles_templates_dir() -> Path:
    return repo_root() / "hermes-profiles"


def hermes_skills_dir() -> Path:
    return repo_root() / "hermes-skills"


def hermes_skill_path(filename: str) -> Path:
    if filename not in HERMES_SKILL_FILENAMES:
        names = ", ".join(HERMES_SKILL_FILENAMES)
        raise ValueError(f"Unknown Hermes skill {filename!r}; expected one of: {names}")
    return hermes_skills_dir() / filename


def setup_script_path() -> Path:
    return repo_root() / "scripts" / _SETUP_SCRIPT_NAME


def profile_template_dir(profile_name: str) -> Path:
    _validate_profile_name(profile_name)
    return profiles_templates_dir() / profile_name


def profile_template_path(profile_name: str, filename: str) -> Path:
    return profile_template_dir(profile_name) / filename


def resolve_hermes_home(hermes_home: str | Path | None = None) -> Path:
    if hermes_home is None:
        hermes_home = os.environ.get("HERMES_HOME", "~/.hermes")
    return Path(hermes_home).expanduser().resolve()


def hermes_profile_dir(profile_name: str, *, hermes_home: Path | None = None) -> Path:
    _validate_profile_name(profile_name)
    return resolve_hermes_home(hermes_home) / "profiles" / profile_name


def profile_exists(profile_name: str, *, hermes_home: Path | None = None) -> bool:
    return hermes_profile_dir(profile_name, hermes_home=hermes_home).is_dir()


def hermes_installed(*, hermes_bin: str | None = None) -> bool:
    if hermes_bin is not None:
        return bool(hermes_bin)
    return shutil.which("hermes") is not None


def profile_commands_available(*, hermes_bin: str | None = None) -> bool:
    hermes = hermes_bin or shutil.which("hermes")
    if not hermes:
        return False

    for subcommand in _PROFILE_HELP_COMMANDS:
        result = run_command([hermes, *subcommand, "-h"])
        if not result.ok:
            return False
    return True


def render_setup_command(
    *,
    dry_run: bool = False,
    report_file: Path | None = None,
) -> list[str]:
    command = [str(setup_script_path())]
    if dry_run:
        command.append("--dry-run")
    if report_file is not None:
        command.extend(["--report", str(report_file)])
    return command


def _validate_profile_name(profile_name: str) -> None:
    if profile_name not in PROFILE_NAMES:
        names = ", ".join(PROFILE_NAMES)
        raise ValueError(f"Unknown Hermes profile {profile_name!r}; expected one of: {names}")
