import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner

from hoca.cli import main
from hoca.fleet_contracts import HocaFleetTask, HocaLane, HocaProject
from hoca.fleet_registry import FleetRegistry

CLI_COMMANDS = {
    "doctor": "Check local HOCA dependencies",
    "init-project": "Install HOCA project-level templates",
    "project": "Manage registered HOCA projects",
    "task": "Manage HOCA tasks",
    "scheduler": "Manage the HOCA scheduler",
    "fleet": "Manage fleet-level HOCA state",
    "run": "Run a HOCA task against a target repository",
    "issue": "Run a HOCA task for a GitHub issue",
    "lane": "Manage and communicate with HOCA lanes",
    "kanban-init": "Experimental",
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
    assert "project" in result.output
    assert "task" in result.output
    assert "scheduler" in result.output
    assert "fleet" in result.output
    assert "run" in result.output
    assert "issue" in result.output
    assert "setup-profiles" in result.output
    assert "report" in result.output
    assert "lane" in result.output


def test_cli_commands_remain_registered() -> None:
    for command, help_text in CLI_COMMANDS.items():
        result = CliRunner().invoke(main, [command, "--help"])

        assert result.exit_code == 0
        assert help_text in result.output


def _seed_lane_for_cli(
    tmp_path: Path,
    *,
    lane_id: str,
    lane_status: str = "running",
) -> tuple[Path, Path]:
    control_root = tmp_path / "control"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HOCA Test"], cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    registry = FleetRegistry(control_root=control_root)
    registry.create_project(
        HocaProject(
            project_id="project-1",
            repo_path=str(repo),
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    registry.create_task(
        HocaFleetTask(
            task_id="task-1",
            project_id="project-1",
            status="queued",
            readiness="not_ready",
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    run_dir = repo / "lane" / lane_id
    registry.create_lane(
        HocaLane(
            lane_id=lane_id,
            task_id="task-1",
            project_id="project-1",
            status=lane_status,
            branch="lane-task-1",
            run_dir=f"lane/{lane_id}",
            attempt_number=0,
            created_at="2026-06-05T00:00:00Z",
            updated_at="2026-06-05T00:00:00Z",
        )
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    return control_root, run_dir


def _seed_project_repo(tmp_path: Path, repo_name: str = "project") -> Path:
    repo = tmp_path / repo_name
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "hoca@example.test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HOCA Test"], cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_project_help_displays_group_help() -> None:
    result = CliRunner().invoke(main, ["project", "--help"])

    assert result.exit_code == 0
    assert "Manage registered HOCA projects." in result.output
    assert "add" in result.output
    assert "list" in result.output
    assert "show" in result.output
    assert "doctor" in result.output
    assert "remove" in result.output


def test_project_add_list_show_doctor_and_remove_use_temp_control_root(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    repo = _seed_project_repo(tmp_path, "project-one")
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    add_result = CliRunner().invoke(
        main,
        ["project", "add", str(repo), "--name", "App One", "--max-parallel-tasks", "3"],
        env=env,
    )

    assert add_result.exit_code == 0
    assert "Project added: project-one" in add_result.output

    registry = FleetRegistry(control_root=control_root)
    project = registry.get_project("project-one")
    assert project is not None
    assert project.display_name == "App One"
    assert project.max_parallel_tasks == 3

    list_result = CliRunner().invoke(main, ["project", "list"], env=env)
    assert list_result.exit_code == 0
    assert "PROJECT_ID" in list_result.output
    assert "project-one" in list_result.output
    assert "App One" in list_result.output

    show_result = CliRunner().invoke(main, ["project", "show", "project-one"], env=env)
    assert show_result.exit_code == 0
    assert "Project ID: project-one" in show_result.output
    assert f"Repository: {repo}" in show_result.output
    assert "Max Parallel Tasks: 3" in show_result.output

    doctor_result = CliRunner().invoke(main, ["project", "doctor"], env=env)
    assert doctor_result.exit_code == 0
    assert "Project doctor OK for 1 project(s)." in doctor_result.output

    remove_result = CliRunner().invoke(main, ["project", "remove", "project-one"], env=env)
    assert remove_result.exit_code == 0
    assert "Project removed: project-one" in remove_result.output
    assert registry.get_project("project-one") is None


def test_task_help_displays_group_help() -> None:
    result = CliRunner().invoke(main, ["task", "--help"])

    assert result.exit_code == 0
    assert "Manage HOCA tasks." in result.output
    assert "create" in result.output
    assert "list" in result.output
    assert "show" in result.output
    assert "cancel" in result.output
    assert "block" in result.output


def test_task_create_list_show_cancel_and_block_use_temp_control_root(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    repo = _seed_project_repo(tmp_path, "project-two")
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    project_result = CliRunner().invoke(
        main,
        ["project", "add", str(repo), "--project-id", "project-two"],
        env=env,
    )
    assert project_result.exit_code == 0

    create_alpha = CliRunner().invoke(
        main,
        [
            "task",
            "create",
            "project-two",
            "Implement task lifecycle",
            "--task-id",
            "task-alpha",
            "--description",
            "Detailed notes",
            "--goal",
            "Ship feature",
            "--priority",
            "2",
        ],
        env=env,
    )
    assert create_alpha.exit_code == 0
    assert "Task created: task-alpha" in create_alpha.output

    create_beta = CliRunner().invoke(
        main,
        [
            "task",
            "create",
            "project-two",
            "Cancel me",
            "--task-id",
            "task-beta",
        ],
        env=env,
    )
    assert create_beta.exit_code == 0

    registry = FleetRegistry(control_root=control_root)
    alpha = registry.get_task("task-alpha")
    assert alpha is not None
    assert alpha.project_id == "project-two"
    assert alpha.title == "Implement task lifecycle"
    assert alpha.goal == "Ship feature"
    assert alpha.priority == 2

    list_result = CliRunner().invoke(
        main,
        ["task", "list", "--project-id", "project-two", "--status", "queued"],
        env=env,
    )
    assert list_result.exit_code == 0
    assert "TASK_ID" in list_result.output
    assert "task-alpha" in list_result.output
    assert "task-beta" in list_result.output

    show_result = CliRunner().invoke(main, ["task", "show", "task-alpha"], env=env)
    assert show_result.exit_code == 0
    assert "Task ID: task-alpha" in show_result.output
    assert "Project ID: project-two" in show_result.output
    assert "Goal: Ship feature" in show_result.output

    block_result = CliRunner().invoke(main, ["task", "block", "task-alpha"], env=env)
    assert block_result.exit_code == 0
    assert "Task blocked: task-alpha" in block_result.output

    cancel_result = CliRunner().invoke(main, ["task", "cancel", "task-beta"], env=env)
    assert cancel_result.exit_code == 0
    assert "Task cancelled: task-beta" in cancel_result.output

    blocked = registry.get_task("task-alpha")
    cancelled = registry.get_task("task-beta")
    assert blocked is not None
    assert cancelled is not None
    assert blocked.status == "blocked"
    assert blocked.readiness == "blocked"
    assert cancelled.status == "cancelled"

    filtered_list = CliRunner().invoke(
        main,
        [
            "task",
            "list",
            "--project-id",
            "project-two",
            "--status",
            "blocked",
            "--status",
            "cancelled",
        ],
        env=env,
    )
    assert filtered_list.exit_code == 0
    assert "task-alpha" in filtered_list.output
    assert "task-beta" in filtered_list.output


def test_task_create_rejects_unknown_project_id(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    result = CliRunner().invoke(
        main,
        ["task", "create", "missing-project", "Do the thing"],
        env=env,
    )

    assert result.exit_code != 0
    assert "Project not found: missing-project" in result.output


def test_scheduler_help_displays_group_help() -> None:
    result = CliRunner().invoke(main, ["scheduler", "--help"])

    assert result.exit_code == 0
    assert "Manage the HOCA scheduler." in result.output
    assert "tick" in result.output
    assert "start" in result.output
    assert "status" in result.output


def test_scheduler_tick_launches_lane_with_temp_control_root(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    repo = _seed_project_repo(tmp_path, "project-three")
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    add_project = CliRunner().invoke(
        main, ["project", "add", str(repo), "--project-id", "project-three"], env=env
    )
    assert add_project.exit_code == 0

    add_task = CliRunner().invoke(
        main,
        [
            "task",
            "create",
            "project-three",
            "Launch me",
            "--task-id",
            "task-launch",
        ],
        env=env,
    )
    assert add_task.exit_code == 0

    tick_result = CliRunner().invoke(main, ["scheduler", "tick"], env=env)
    assert tick_result.exit_code == 0
    assert "launch" in tick_result.output
    assert "task-launch" in tick_result.output

    registry = FleetRegistry(control_root=control_root)
    task = registry.get_task("task-launch")
    assert task is not None
    assert task.status == "running"
    lanes = registry.list_lanes(task_id="task-launch")
    assert len(lanes) == 1
    assert lanes[0].project_id == "project-three"


def test_lane_list_show_logs_and_stop_use_temp_control_root(tmp_path: Path) -> None:
    control_root, run_dir = _seed_lane_for_cli(tmp_path, lane_id="lane-task-5-01")
    (run_dir / "worker.log").write_text("worker log\n", encoding="utf-8")
    (run_dir / "nested").mkdir()
    (run_dir / "nested" / "adapter.log").write_text("adapter log\n", encoding="utf-8")
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    list_result = CliRunner().invoke(main, ["lane", "list", "--project-id", "project-1"], env=env)
    assert list_result.exit_code == 0
    assert "LANE_ID" in list_result.output
    assert "lane-task-5-01" in list_result.output

    show_result = CliRunner().invoke(main, ["lane", "show", "lane-task-5-01"], env=env)
    assert show_result.exit_code == 0
    assert "Lane ID: lane-task-5-01" in show_result.output
    assert "Run Dir:" in show_result.output

    logs_result = CliRunner().invoke(main, ["lane", "logs", "lane-task-5-01"], env=env)
    assert logs_result.exit_code == 0
    assert str(run_dir / "worker.log") in logs_result.output
    assert str(run_dir / "nested" / "adapter.log") in logs_result.output

    stop_result = CliRunner().invoke(main, ["lane", "stop", "lane-task-5-01"], env=env)
    assert stop_result.exit_code == 0
    assert "Lane stopped: lane-task-5-01" in stop_result.output

    registry = FleetRegistry(control_root=control_root)
    lane = registry.get_lane("lane-task-5-01")
    assert lane is not None
    assert lane.status == "cleaned"


def test_fleet_help_displays_group_help() -> None:
    result = CliRunner().invoke(main, ["fleet", "--help"])

    assert result.exit_code == 0
    assert "Manage fleet-level HOCA state." in result.output
    assert "status" in result.output
    assert "doctor" in result.output
    assert "report" in result.output
    assert "cleanup" in result.output


def test_fleet_status_report_and_cleanup_use_temp_control_root(tmp_path: Path) -> None:
    control_root, _ = _seed_lane_for_cli(tmp_path, lane_id="lane-task-6-01")
    env = {"HOCA_CONTROL_ROOT": str(control_root)}

    stop_result = CliRunner().invoke(main, ["lane", "stop", "lane-task-6-01"], env=env)
    assert stop_result.exit_code == 0

    status_result = CliRunner().invoke(main, ["fleet", "status"], env=env)
    assert status_result.exit_code == 0
    assert "Projects: 1" in status_result.output
    assert "Queued Tasks: 1" in status_result.output
    assert "Running Lanes: 0" in status_result.output
    assert "Blocked Lanes: 0" in status_result.output
    assert "Ready PRs: 0" in status_result.output

    report_result = CliRunner().invoke(main, ["fleet", "report"], env=env)
    assert report_result.exit_code == 0
    report_path = control_root / "fleet-report.md"
    assert report_path.is_file()
    assert "Fleet report written:" in report_result.output
    assert "HOCA Fleet Report" in report_path.read_text(encoding="utf-8")

    dry_run_result = CliRunner().invoke(main, ["fleet", "cleanup", "--dry-run"], env=env)
    assert dry_run_result.exit_code == 0
    assert "Would remove cleaned lane: lane-task-6-01" in dry_run_result.output

    cleanup_result = CliRunner().invoke(main, ["fleet", "cleanup"], env=env)
    assert cleanup_result.exit_code == 0
    assert "Removed cleaned lane: lane-task-6-01" in cleanup_result.output

    registry = FleetRegistry(control_root=control_root)
    assert registry.get_lane("lane-task-6-01") is None


def test_lane_send_helps_command() -> None:
    result = CliRunner().invoke(main, ["lane", "send", "--help"])

    assert result.exit_code == 0
    assert "Send a manager-approved redirection to a lane session." in result.output
    assert "--dry-run" in result.output


def test_lane_send_transmits_message_and_logs(tmp_path, monkeypatch) -> None:
    control_root, run_dir = _seed_lane_for_cli(tmp_path, lane_id="lane-task-1-01")
    calls: list[tuple[str, str]] = []

    def fake_send_to_session(session_name: str, message: str) -> None:
        calls.append((session_name, message))

    monkeypatch.setattr("hoca.cli.send_to_session", fake_send_to_session)
    monkeypatch.setenv("HOCA_CONTROL_ROOT", str(control_root))

    result = CliRunner().invoke(main, ["lane", "send", "lane-task-1-01", "continue"])

    assert result.exit_code == 0
    assert calls == [("lane-task-1-01", "continue")]
    assert "Message sent to lane: lane-task-1-01" in result.output
    assert (run_dir / "lane-send.log").is_file()


def test_lane_send_dry_run_does_not_dispatch(tmp_path, monkeypatch) -> None:
    control_root, run_dir = _seed_lane_for_cli(tmp_path, lane_id="lane-task-2-01")
    calls: list[tuple[str, str]] = []

    def fake_send_to_session(session_name: str, message: str) -> None:
        calls.append((session_name, message))

    monkeypatch.setattr("hoca.cli.send_to_session", fake_send_to_session)
    monkeypatch.setenv("HOCA_CONTROL_ROOT", str(control_root))

    result = CliRunner().invoke(
        main,
        ["lane", "send", "lane-task-2-01", "hold on", "--dry-run"],
    )

    assert result.exit_code == 0
    assert calls == []
    assert "Dry run: not sent lane send to lane-task-2-01" in result.output
    assert "dry-run=hold on" in (run_dir / "lane-send.log").read_text(encoding="utf-8")


def test_lane_send_blocks_secret_like_messages(tmp_path, monkeypatch) -> None:
    control_root, _ = _seed_lane_for_cli(tmp_path, lane_id="lane-task-3-01")
    calls: list[tuple[str, str]] = []

    def fake_send_to_session(session_name: str, message: str) -> None:
        calls.append((session_name, message))

    monkeypatch.setattr("hoca.cli.send_to_session", fake_send_to_session)
    monkeypatch.setenv("HOCA_CONTROL_ROOT", str(control_root))

    result = CliRunner().invoke(main, ["lane", "send", "lane-task-3-01", "api_key=abc123"])
    assert result.exit_code != 0
    assert "secret-like content" in result.output
    assert calls == []


def test_lane_send_rejects_blocked_lane(tmp_path, monkeypatch) -> None:
    control_root, _ = _seed_lane_for_cli(
        tmp_path,
        lane_id="lane-task-4-01",
        lane_status="cleaned",
    )
    calls: list[tuple[str, str]] = []

    def fake_send_to_session(session_name: str, message: str) -> None:
        calls.append((session_name, message))

    monkeypatch.setattr("hoca.cli.send_to_session", fake_send_to_session)
    monkeypatch.setenv("HOCA_CONTROL_ROOT", str(control_root))

    result = CliRunner().invoke(main, ["lane", "send", "lane-task-4-01", "continue"])
    assert result.exit_code != 0
    assert calls == []
    assert "Cannot send to lane with status 'cleaned'" in result.output


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

    result = CliRunner().invoke(
        main, ["run", str(project_path), "A task", "--dev-branch", "develop"]
    )

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
