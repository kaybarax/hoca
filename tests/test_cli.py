from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from hoca.cli import main


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

    result = CliRunner().invoke(
        main, ["run", str(project_path), "A task", "--auto-merge"]
    )

    assert result.exit_code == 0
    assert calls == [
        ("run-hoca-task.sh", [str(project_path), "A task", "--auto-merge"])
    ]


def test_run_forwards_notify_telegram_flag(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(
        main, ["run", str(project_path), "A task", "--notify-telegram"]
    )

    assert result.exit_code == 0
    assert calls == [
        ("run-hoca-task.sh", [str(project_path), "A task", "--notify-telegram"])
    ]


def test_issue_constructs_task_and_passes_issue_id(
    monkeypatch, tmp_path: Path
) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    calls: list[tuple[str, list[str]]] = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(
        main, ["issue", str(project_path), "42", "Fix the login bug"]
    )

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


def test_issue_forwards_auto_merge_and_notify_flags(
    monkeypatch, tmp_path: Path
) -> None:
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


def test_run_script_raises_on_missing_script(tmp_path: Path) -> None:
    from hoca.cli import run_script

    with patch("hoca.cli.repo_root", return_value=tmp_path):
        result = CliRunner().invoke(main, ["--help"])
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
