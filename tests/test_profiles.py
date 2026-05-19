from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hoca.profiles import (
    HERMES_SKILL_FILENAMES,
    PROFILE_NAMES,
    PROFILE_REVIEWER,
    hermes_installed,
    hermes_profile_dir,
    hermes_skill_path,
    hermes_skills_dir,
    profile_commands_available,
    profile_exists,
    profile_template_dir,
    profile_template_path,
    profiles_templates_dir,
    render_setup_command,
    resolve_hermes_home,
    setup_script_path,
)
from hoca.subprocess_utils import CommandResult


def test_profile_name_constants() -> None:
    assert PROFILE_NAMES == ("hoca-manager", "hoca-worker", "hoca-reviewer")
    assert PROFILE_REVIEWER in PROFILE_NAMES


def test_profiles_templates_dir_points_at_repo_templates() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    templates_dir = profiles_templates_dir()

    assert templates_dir == repo_root / "hermes-profiles"
    assert templates_dir.is_dir()


def test_profile_template_paths_for_each_role() -> None:
    for profile_name in PROFILE_NAMES:
        profile_dir = profile_template_dir(profile_name)
        soul_path = profile_template_path(profile_name, "SOUL.md")
        config_path = profile_template_path(profile_name, "config.example.yaml")

        assert profile_dir == profiles_templates_dir() / profile_name
        assert soul_path == profile_dir / "SOUL.md"
        assert config_path == profile_dir / "config.example.yaml"
        assert soul_path.is_file()
        assert config_path.is_file()


def test_profile_template_dir_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="Unknown Hermes profile"):
        profile_template_dir("hoca-unknown")


def test_setup_script_path_points_at_setup_script() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = setup_script_path()

    assert script == repo_root / "scripts" / "setup-hermes-profiles.sh"
    assert script.is_file()


def test_hermes_skills_dir_points_at_repo_skills() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert hermes_skills_dir() == repo_root / "hermes-skills"
    assert hermes_skills_dir().is_dir()


def test_hermes_skill_paths_for_each_role_skill() -> None:
    for filename in HERMES_SKILL_FILENAMES:
        path = hermes_skill_path(filename)
        assert path.parent == hermes_skills_dir()
        assert path.is_file()


def test_hermes_skill_path_rejects_unknown_filename() -> None:
    with pytest.raises(ValueError, match="Unknown Hermes skill"):
        hermes_skill_path("hoca-unknown.md")


def test_resolve_hermes_home_expands_tilde() -> None:
    assert resolve_hermes_home("~/custom-hermes") == Path.home() / "custom-hermes"


def test_resolve_hermes_home_uses_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert resolve_hermes_home() == Path.home() / ".hermes"


def test_resolve_hermes_home_honors_explicit_override() -> None:
    hermes_home = Path("/tmp/hoca-hermes-home")
    assert resolve_hermes_home(hermes_home) == hermes_home.resolve()


def test_profile_exists_checks_profile_directory(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes-home"
    profile_dir = hermes_home / "profiles" / "hoca-manager"
    profile_dir.mkdir(parents=True)

    assert profile_exists("hoca-manager", hermes_home=hermes_home) is True
    assert profile_exists("hoca-worker", hermes_home=hermes_home) is False


def test_hermes_profile_dir_builds_expected_path(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes-home"

    assert hermes_profile_dir("hoca-reviewer", hermes_home=hermes_home) == (
        hermes_home / "profiles" / "hoca-reviewer"
    )


def test_hermes_installed_uses_which_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hoca.profiles.shutil.which", lambda _: None)
    assert hermes_installed() is False

    monkeypatch.setattr("hoca.profiles.shutil.which", lambda _: "/usr/local/bin/hermes")
    assert hermes_installed() is True


def test_hermes_installed_accepts_explicit_binary_path() -> None:
    assert hermes_installed(hermes_bin="/opt/hermes/bin/hermes") is True
    assert hermes_installed(hermes_bin="") is False


def test_profile_commands_available_requires_all_profile_help_commands() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run_command(command, *, cwd=None):
        calls.append(tuple(command))
        if tuple(command[1:3]) == ("profile", "create"):
            return CommandResult(command=tuple(command), returncode=1, stdout="", stderr="")
        return CommandResult(command=tuple(command), returncode=0, stdout="", stderr="")

    with patch("hoca.profiles.run_command", fake_run_command):
        available = profile_commands_available(hermes_bin="/usr/bin/hermes")

    assert available is False
    assert len(calls) == 2
    assert calls[0] == ("/usr/bin/hermes", "profile", "list", "-h")
    assert calls[1] == ("/usr/bin/hermes", "profile", "create", "-h")


def test_profile_commands_available_returns_false_without_hermes() -> None:
    with patch("hoca.profiles.shutil.which", lambda _: None):
        assert profile_commands_available() is False


def test_profile_commands_available_returns_true_when_help_succeeds() -> None:
    def fake_run_command(command, *, cwd=None):
        return CommandResult(command=tuple(command), returncode=0, stdout="", stderr="")

    with patch("hoca.profiles.run_command", fake_run_command):
        assert profile_commands_available(hermes_bin="/usr/bin/hermes") is True


def test_render_setup_command_points_at_setup_script() -> None:
    command = render_setup_command()

    assert command[0] == str(setup_script_path())
    assert command[1:] == []


def test_render_setup_command_supports_dry_run_and_report_file(tmp_path: Path) -> None:
    report_file = tmp_path / "custom-report.txt"

    command = render_setup_command(dry_run=True, report_file=report_file)

    assert command == [
        str(setup_script_path()),
        "--dry-run",
        "--report",
        str(report_file),
    ]
