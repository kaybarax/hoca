from pathlib import Path

from click.testing import CliRunner

from hoca.cli import main


def test_cli_main_is_callable() -> None:
    assert callable(main)


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
    calls = []

    def fake_run_script(script_name: str, args: list[str]) -> None:
        calls.append((script_name, args))

    monkeypatch.setattr("hoca.cli.run_script", fake_run_script)

    result = CliRunner().invoke(main, ["run", str(project_path), "Task with spaces"])

    assert result.exit_code == 0
    assert calls == [("run-hoca-task.sh", [str(project_path), "Task with spaces"])]
