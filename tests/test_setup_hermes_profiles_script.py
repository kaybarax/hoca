from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup-hermes-profiles.sh"
PROFILE_NAMES = ("hoca-manager", "hoca-worker", "hoca-reviewer")


def hermes_available() -> bool:
    return shutil.which("hermes") is not None


def run_setup(
    *,
    hermes_home: Path,
    dry_run: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    if extra_env:
        env.update(extra_env)
    args = [str(SETUP_SCRIPT)]
    if dry_run:
        args.append("--dry-run")
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_setup_script_documents_required_behavior() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "hermes profile create" in script
    assert "--no-skills" in script
    assert "--dry-run" in script
    assert "HERMES_SKILLS_DIR" in script
    assert "DEFAULT_HERMES_SOUL" in script
    assert "setup-hermes-profiles-report.txt" in script
    for profile_name in PROFILE_NAMES:
        assert profile_name in script


def test_setup_script_dry_run_never_writes_report(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    report_path = REPO_ROOT / ".hoca-runtime" / "setup-hermes-profiles-report.txt"
    if report_path.is_file():
        report_path.unlink()

    result = run_setup(hermes_home=hermes_home, dry_run=True)

    assert result.returncode == 0, result.stderr
    assert "[DRY-RUN]" in result.stdout
    assert "hermes profile create hoca-manager" in result.stdout
    assert not report_path.exists()
    assert not list(hermes_home.glob("profiles/*"))


def test_setup_script_is_idempotent_with_fake_hermes_and_temp_home(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hermes = fake_bin / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "profile" && "${2:-}" =~ ^(list|create|show)$ && "${3:-}" == "-h" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "profile" && "${2:-}" == "create" ]]; then\n'
        '  profile="${3:?}"\n'
        '  mkdir -p "${HERMES_HOME:?}/profiles/${profile}"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "profile" && "${2:-}" == "list" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "profile" && "${2:-}" == "show" ]]; then exit 0; fi\n'
        "exit 2\n",
        encoding="utf-8",
    )
    hermes.chmod(hermes.stat().st_mode | 0o700)
    hermes_home = tmp_path / "hermes-home"
    workspace_root = tmp_path / "projects"
    report_path = tmp_path / "setup-report.txt"

    first = run_setup(
        hermes_home=hermes_home,
        extra_env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "HOCA_WORKSPACE_ROOT": str(workspace_root),
            "HOME": str(tmp_path / "home"),
        },
    )
    second = subprocess.run(
        [str(SETUP_SCRIPT), "--report", str(report_path)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ.copy(),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "HERMES_HOME": str(hermes_home),
            "HOCA_WORKSPACE_ROOT": str(workspace_root),
            "HOME": str(tmp_path / "home"),
        },
    )

    assert first.returncode == 0, first.stderr + first.stdout
    assert second.returncode == 0, second.stderr + second.stdout
    assert "already matches HOCA template" in second.stdout
    assert "already references HOCA hermes-skills" in second.stdout
    assert report_path.is_file()
    for profile_name in PROFILE_NAMES:
        profile_dir = hermes_home / "profiles" / profile_name
        assert (profile_dir / "SOUL.md").is_file()
        assert (profile_dir / "config.yaml").is_file()


@pytest.mark.skipif(not hermes_available(), reason="hermes CLI not installed")
def test_setup_script_is_idempotent_with_temp_hermes_home(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()

    first = run_setup(
        hermes_home=hermes_home,
        extra_env={"HOCA_WORKSPACE_ROOT": str(workspace_root)},
    )
    assert first.returncode == 0, first.stderr + first.stdout

    for profile_name in PROFILE_NAMES:
        profile_dir = hermes_home / "profiles" / profile_name
        assert profile_dir.is_dir(), f"missing profile dir: {profile_dir}"
        soul = profile_dir / "SOUL.md"
        config = profile_dir / "config.yaml"
        assert soul.is_file()
        assert config.is_file()
        assert profile_name in soul.read_text(encoding="utf-8")
        assert str(REPO_ROOT / "hermes-skills") in config.read_text(encoding="utf-8")
        assert str(workspace_root) in config.read_text(encoding="utf-8")

    first_soul_hashes = {
        name: _sha256(hermes_home / "profiles" / name / "SOUL.md")
        for name in PROFILE_NAMES
    }
    first_config_hashes = {
        name: _sha256(hermes_home / "profiles" / name / "config.yaml")
        for name in PROFILE_NAMES
    }

    second = run_setup(
        hermes_home=hermes_home,
        extra_env={"HOCA_WORKSPACE_ROOT": str(workspace_root)},
    )
    assert second.returncode == 0, second.stderr + second.stdout
    assert "preserved" in second.stdout.lower() or "already" in second.stdout.lower()

    for profile_name in PROFILE_NAMES:
        profile_dir = hermes_home / "profiles" / profile_name
        assert _sha256(profile_dir / "SOUL.md") == first_soul_hashes[profile_name]
        assert _sha256(profile_dir / "config.yaml") == first_config_hashes[profile_name]


@pytest.mark.skipif(not hermes_available(), reason="hermes CLI not installed")
def test_setup_script_preserves_user_modified_soul(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()

    first = run_setup(hermes_home=hermes_home)
    assert first.returncode == 0, first.stderr + first.stdout

    soul_file = hermes_home / "profiles" / "hoca-manager" / "SOUL.md"
    custom_soul = "# Custom manager soul\nUser edited identity.\n"
    soul_file.write_text(custom_soul, encoding="utf-8")

    second = run_setup(hermes_home=hermes_home)
    assert second.returncode == 0, second.stderr + second.stdout
    assert soul_file.read_text(encoding="utf-8") == custom_soul
    assert "preserved" in second.stdout.lower()


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
