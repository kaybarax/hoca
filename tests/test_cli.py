import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner

from hoca.cli import main

CLI_COMMANDS = {
    "doctor": "Check local HOCA dependencies",
    "init-project": "Install HOCA project-level templates",
    "run": "Run a HOCA task against a target repository",
    "issue": "Run a HOCA task for a GitHub issue",
}

DIRECT_ENTRYPOINTS = [
    "bin/hoca",
    "scripts/hoca-doctor.sh",
    "scripts/init-project.sh",
    "scripts/run-hoca-task.sh",
    "scripts/run-openhands-task.sh",
    "scripts/review-with-openhands.sh",
]


def test_cli_main_is_callable() -> None:
    assert callable(main)


def test_cli_help_displays_group_help() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "HOCA local autonomous engineering toolkit." in result.output
    assert "doctor" in result.output
    assert "init-project" in result.output
    assert "run" in result.output
    assert "issue" in result.output
    assert "setup-profiles" in result.output
    assert "report" in result.output


def test_cli_commands_remain_registered() -> None:
    for command, help_text in CLI_COMMANDS.items():
        result = CliRunner().invoke(main, [command, "--help"])

        assert result.exit_code == 0
        assert help_text in result.output


def test_direct_entrypoints_remain_executable_and_parseable() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    for relative_path in DIRECT_ENTRYPOINTS:
        entrypoint = repo_root / relative_path

        assert entrypoint.is_file()
        assert os.access(entrypoint, os.X_OK)

        result = subprocess.run(
            ["bash", "-n", str(entrypoint)],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr


def test_setup_profiles_help_displays_command_help() -> None:
    result = CliRunner().invoke(main, ["setup-profiles", "--help"])

    assert result.exit_code == 0
    assert "Install or update HOCA Hermes role profiles" in result.output
    assert "--dry-run" in result.output


def test_setup_profiles_calls_script(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["setup-profiles"])

    assert result.exit_code == 0
    assert calls == [("setup-hermes-profiles.sh", [])]


def test_setup_profiles_forwards_dry_run_flag(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["setup-profiles", "--dry-run"])

    assert result.exit_code == 0
    assert calls == [("setup-hermes-profiles.sh", ["--dry-run"])]


def test_setup_profiles_reports_missing_script(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("hoca.cli.repo_root", lambda: tmp_path)

    result = CliRunner().invoke(main, ["setup-profiles"])

    assert result.exit_code != 0
    assert "Missing script" in result.output


def test_setup_profiles_reports_script_failure(monkeypatch, tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    failing_script = scripts_dir / "setup-hermes-profiles.sh"
    failing_script.write_text("#!/bin/sh\nexit 2\n")
    failing_script.chmod(0o755)
    monkeypatch.setattr("hoca.cli.repo_root", lambda: tmp_path)

    result = CliRunner().invoke(main, ["setup-profiles"])

    assert result.exit_code != 0
    assert "Command failed with exit code 2" in result.output


def test_bin_hoca_displays_help() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["HOCA_PYTHON"] = sys.executable

    result = subprocess.run(
        [str(repo_root / "bin" / "hoca"), "--help"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "HOCA local autonomous engineering toolkit." in result.stdout
    assert "doctor" in result.stdout


def test_doctor_reports_success(monkeypatch) -> None:
    class OkReport:
        ok = True
        failures = ()

    monkeypatch.setattr("hoca.cli.run_doctor", lambda: OkReport())

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0


def test_doctor_reports_wrapper_failures(monkeypatch) -> None:
    class FailedReport:
        ok = False
        failures = (object(), object())

    monkeypatch.setattr("hoca.cli.run_doctor", lambda: FailedReport())

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code != 0
    assert "Doctor found 2 critical failure(s)." in result.output


def test_doctor_reports_missing_script(monkeypatch) -> None:
    def fake_run_doctor():
        raise FileNotFoundError("Missing doctor script")

    monkeypatch.setattr("hoca.cli.run_doctor", fake_run_doctor)

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code != 0
    assert "Missing doctor script" in result.output


def test_init_project_calls_script(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["init-project", str(project_path)])

    assert result.exit_code == 0
    assert calls == [("init-project.sh", [str(project_path)])]


def test_init_project_rejects_non_git_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "not-a-repo"
    project_path.mkdir()

    result = CliRunner().invoke(main, ["init-project", str(project_path)])

    assert result.exit_code != 0
    assert "not a Git repository" in result.output


def test_run_reports_missing_target_repository() -> None:
    result = CliRunner().invoke(main, ["run", "/missing/hoca-target", "Do the thing"])

    assert result.exit_code != 0
    assert "Target repository does not exist" in result.output


def test_run_passes_task_text_as_one_argument(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["run", str(project_path), "Task with spaces"])

    assert result.exit_code == 0
    assert calls == [("run-hoca-task.sh", [str(project_path), "Task with spaces"])]


def test_run_forwards_auto_merge_flag(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["run", str(project_path), "A task", "--auto-merge"])

    assert result.exit_code == 0
    assert calls == [("run-hoca-task.sh", [str(project_path), "A task", "--auto-merge"])]


def test_run_forwards_notify_telegram_flag(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["run", str(project_path), "A task", "--notify-telegram"])

    assert result.exit_code == 0
    assert calls == [("run-hoca-task.sh", [str(project_path), "A task", "--notify-telegram"])]


def test_run_forwards_dev_branch_flag(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["run", str(project_path), "A task", "--dev-branch", "develop"])

    assert result.exit_code == 0
    assert calls == [("run-hoca-task.sh", [str(project_path), "A task", "--dev-branch", "develop"])]


def test_issue_constructs_task_and_passes_issue_id(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["issue", str(project_path), "42", "Fix the login bug"])

    assert result.exit_code == 0
    assert calls == [
        (
            "run-hoca-task.sh",
            [
                str(project_path),
                "Fix GitHub issue #42: Fix the login bug",
                "--issue-id",
                "42",
            ],
        )
    ]


def test_issue_forwards_auto_merge_and_notify_flags(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(
        main,
        [
            "issue",
            str(project_path),
            "7",
            "Add tests",
            "--auto-merge",
            "--notify-telegram",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "run-hoca-task.sh",
            [
                str(project_path),
                "Fix GitHub issue #7: Add tests",
                "--issue-id",
                "7",
                "--auto-merge",
                "--notify-telegram",
            ],
        )
    ]


def test_issue_forwards_dev_branch_flag(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(
        main,
        ["issue", str(project_path), "7", "Add tests", "--dev-branch", "develop"],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "run-hoca-task.sh",
            [
                str(project_path),
                "Fix GitHub issue #7: Add tests",
                "--issue-id",
                "7",
                "--dev-branch",
                "develop",
            ],
        )
    ]


def test_kanban_init_appears_in_help() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "kanban-init" in result.output


def test_kanban_run_appears_in_help() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "kanban-run" in result.output


def test_kanban_watch_appears_in_help() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "kanban-watch" in result.output


def test_kanban_init_help_shows_experimental() -> None:
    result = CliRunner().invoke(main, ["kanban-init", "--help"])

    assert result.exit_code == 0
    assert "Experimental" in result.output


def test_kanban_run_help_shows_experimental() -> None:
    result = CliRunner().invoke(main, ["kanban-run", "--help"])

    assert result.exit_code == 0
    assert "Experimental" in result.output


def test_kanban_watch_help_shows_experimental() -> None:
    result = CliRunner().invoke(main, ["kanban-watch", "--help"])

    assert result.exit_code == 0
    assert "Experimental" in result.output


def test_kanban_init_calls_script(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["kanban-init", str(project_path)])

    assert result.exit_code == 0
    assert calls == [("kanban-init.sh", [str(project_path)])]


def test_kanban_run_calls_script_with_task(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["kanban-run", str(project_path), "Add login feature"])

    assert result.exit_code == 0
    assert calls == [("kanban-run.sh", [str(project_path), "Add login feature"])]


def test_kanban_watch_calls_script(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["kanban-watch", str(project_path)])

    assert result.exit_code == 0
    assert calls == [("kanban-watch.sh", [str(project_path)])]


def test_kanban_init_rejects_non_git_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "not-a-repo"
    project_path.mkdir()

    result = CliRunner().invoke(main, ["kanban-init", str(project_path)])

    assert result.exit_code != 0
    assert "not a Git repository" in result.output


def test_kanban_run_rejects_non_git_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "not-a-repo"
    project_path.mkdir()

    result = CliRunner().invoke(main, ["kanban-run", str(project_path), "A task"])

    assert result.exit_code != 0
    assert "not a Git repository" in result.output


def test_kanban_watch_rejects_non_git_directory(tmp_path: Path) -> None:
    project_path = tmp_path / "not-a-repo"
    project_path.mkdir()

    result = CliRunner().invoke(main, ["kanban-watch", str(project_path)])

    assert result.exit_code != 0
    assert "not a Git repository" in result.output


def test_kanban_init_reports_missing_script(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    monkeypatch.setattr("hoca.cli.repo_root", lambda: tmp_path)

    result = CliRunner().invoke(main, ["kanban-init", str(project_path)])

    assert result.exit_code != 0
    assert "Missing script" in result.output


def test_kanban_init_reports_script_failure(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    failing_script = scripts_dir / "kanban-init.sh"
    failing_script.write_text("#!/bin/sh\nexit 1\n")
    failing_script.chmod(0o755)
    monkeypatch.setattr("hoca.cli.repo_root", lambda: tmp_path)

    result = CliRunner().invoke(main, ["kanban-init", str(project_path)])

    assert result.exit_code != 0
    assert "Command failed with exit code 1" in result.output


def test_report_help_displays_command_help() -> None:
    result = CliRunner().invoke(main, ["report", "--help"])

    assert result.exit_code == 0
    assert "Show or regenerate the task report" in result.output
    assert "--regenerate" in result.output


def test_report_shows_existing_report(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    run_dir = project_path / ".hoca-runtime" / "runs" / "run-12345"
    run_dir.mkdir(parents=True)
    report_file = run_dir / "task-report.md"
    report_file.write_text("## HOCA Task Report\n")

    result = CliRunner().invoke(main, ["report", str(project_path), "run-12345"])

    assert result.exit_code == 0
    assert "Report:" in result.output
    assert "run-12345" in result.output


def test_report_regenerates_when_flag_set(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    run_dir = project_path / ".hoca-runtime" / "runs" / "run-12345"
    run_dir.mkdir(parents=True)
    report_file = run_dir / "task-report.md"
    report_file.write_text("old content")

    result = CliRunner().invoke(main, ["report", str(project_path), "run-12345", "--regenerate"])

    assert result.exit_code == 0
    assert "Report regenerated:" in result.output
    content = report_file.read_text()
    assert "## HOCA Task Report" in content


def test_report_regenerates_when_no_report_exists(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    run_dir = project_path / ".hoca-runtime" / "runs" / "run-12345"
    run_dir.mkdir(parents=True)

    result = CliRunner().invoke(main, ["report", str(project_path), "run-12345"])

    assert result.exit_code == 0
    assert "Report regenerated:" in result.output
    assert (run_dir / "task-report.md").is_file()


def test_report_fails_for_missing_run_dir(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()

    result = CliRunner().invoke(main, ["report", str(project_path), "run-nonexistent"])

    assert result.exit_code != 0
    assert "Run directory not found" in result.output


def test_report_falls_back_to_runtime_archive(tmp_path: Path, monkeypatch) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()

    archive_root = tmp_path / "archives"
    archive_run_dir = archive_root / "repo" / "run-99999"
    archive_run_dir.mkdir(parents=True)
    report_file = archive_run_dir / "task-report.md"
    report_file.write_text("## HOCA Task Report\nArchived run.\n")

    monkeypatch.setenv("HOCA_RUNTIME_ARCHIVE_ROOT", str(archive_root))

    result = CliRunner().invoke(main, ["report", str(project_path), "run-99999"])

    assert result.exit_code == 0
    assert "Report:" in result.output
    assert "run-99999" in result.output


def test_report_regenerates_from_runtime_archive(tmp_path: Path, monkeypatch) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()

    archive_root = tmp_path / "archives"
    archive_run_dir = archive_root / "repo" / "run-99999"
    archive_run_dir.mkdir(parents=True)

    monkeypatch.setenv("HOCA_RUNTIME_ARCHIVE_ROOT", str(archive_root))

    result = CliRunner().invoke(main, ["report", str(project_path), "run-99999"])

    assert result.exit_code == 0
    assert "Report regenerated:" in result.output
    assert (archive_run_dir / "task-report.md").is_file()


def test_report_fails_for_non_repo(tmp_path: Path) -> None:
    project_path = tmp_path / "not-a-repo"
    project_path.mkdir()

    result = CliRunner().invoke(main, ["report", str(project_path), "run-12345"])

    assert result.exit_code != 0
    assert "not a Git repository" in result.output


def test_run_script_raises_on_missing_script(tmp_path: Path) -> None:
    from hoca.cli import run_script

    with patch("hoca.cli.repo_root", return_value=tmp_path):
        CliRunner().invoke(main, ["--help"])
        try:
            run_script("nonexistent.sh", [])
        except Exception as exc:
            assert "Missing script" in str(exc)
        else:
            raise AssertionError("Expected exception for missing script")


def test_run_script_raises_on_nonzero_exit(tmp_path: Path) -> None:
    from hoca.cli import run_script

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    failing_script = scripts_dir / "fail.sh"
    failing_script.write_text("#!/bin/sh\nexit 1\n")
    failing_script.chmod(0o755)

    with patch("hoca.cli.repo_root", return_value=tmp_path):
        try:
            run_script("fail.sh", [])
        except Exception as exc:
            assert "Command failed with exit code 1" in str(exc)
        else:
            raise AssertionError("Expected exception for failing script")
