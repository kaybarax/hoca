from __future__ import annotations

import subprocess
from pathlib import Path
import time

import click

from hoca.doctor import run_doctor
from hoca.fleet_registry import FleetRegistry
from hoca.fleet_contracts import HocaLane
from hoca.paths import repo_root
from hoca.tmux_sessions import AdapterCommandError, _sanitize_session_name, send_to_session


def run_script(script_name: str, args: list[str]) -> None:
    script = repo_root() / "scripts" / script_name
    if not script.exists():
        raise click.ClickException(f"Missing script: {script}")

    command = [str(script), *args]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        command_text = " ".join(command)
        raise click.ClickException(
            f"Command failed with exit code {result.returncode}: {command_text}"
        )


def require_target_repo(project_path: Path) -> Path:
    if not project_path.exists():
        raise click.ClickException(f"Target repository does not exist: {project_path}")
    if not project_path.is_dir():
        raise click.ClickException(f"Target repository is not a directory: {project_path}")
    if not (project_path / ".git").exists():
        raise click.ClickException(f"Target path is not a Git repository: {project_path}")
    return project_path


def _secret_like_message(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("api_key", "api-key", "secret", "token", "password", "private_key"))


def _append_send_log(run_dir: Path, lane_id: str, message: str, *, dry_run: bool) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "lane-send.log"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tag = "dry-run" if dry_run else "sent"
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} {lane_id} {tag}={message}\n")


def _resolve_lane_for_send(lane_id: str, *, control_root: Path | None = None) -> tuple[HocaLane, Path]:
    registry = FleetRegistry(control_root=control_root)
    lane = registry.get_lane(lane_id)
    if lane is None:
        raise click.ClickException(f"Lane not found: {lane_id}")

    if not lane.run_dir:
        raise click.ClickException(f"Lane {lane_id} has no run directory")

    project = registry.get_project(lane.project_id)
    if project is None:
        raise click.ClickException(f"Lane {lane_id} references missing project: {lane.project_id}")

    raw_run_dir = Path(lane.run_dir)
    if raw_run_dir.is_absolute():
        return lane, raw_run_dir

    project_path = Path(project.repo_path)
    return lane, project_path / raw_run_dir


def _can_send_to_lane(lane: HocaLane) -> bool:
    return lane.status not in {"blocked", "failed", "cleaned"}


def _block_secret_like(message: str) -> None:
    if _secret_like_message(message):
        raise click.ClickException("message appears to contain secret-like content")


def _send_to_lane(
    lane_id: str,
    message: str,
    *,
    dry_run: bool,
    control_root: Path | None = None,
) -> Path:
    lane, run_dir = _resolve_lane_for_send(lane_id, control_root=control_root)
    if not _can_send_to_lane(lane):
        raise click.ClickException(f"Cannot send to lane with status '{lane.status}': {lane_id}")

    _block_secret_like(message)
    _append_send_log(run_dir, lane_id, message, dry_run=dry_run)

    if dry_run:
        return run_dir

    try:
        send_to_session(_sanitize_session_name(lane_id), message)
    except AdapterCommandError as error:
        raise click.ClickException(f"Failed to send message for lane {lane_id}: {error}") from error

    return run_dir


@click.group()
def main() -> None:
    """HOCA local autonomous engineering toolkit."""


@main.command()
def doctor() -> None:
    """Check local HOCA dependencies and configuration."""
    try:
        report = run_doctor()
    except FileNotFoundError as error:
        raise click.ClickException(str(error)) from error
    if not report.ok:
        raise click.ClickException(f"Doctor found {len(report.failures)} critical failure(s).")


@main.command("init-project")
@click.argument("project_path", type=click.Path(path_type=Path))
def init_project(project_path: Path) -> None:
    """Install HOCA project-level templates into a target repository."""
    project_path = require_target_repo(project_path)
    run_script("init-project.sh", [str(project_path)])


@main.command("setup-profiles")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Print planned actions without changing files."
)
def setup_profiles(dry_run: bool) -> None:
    """Install or update HOCA Hermes role profiles from repo templates."""
    args = ["--dry-run"] if dry_run else []
    run_script("setup-hermes-profiles.sh", args)


@main.command()
@click.argument("project_path", type=click.Path(path_type=Path))
@click.argument("task")
@click.option("--auto-merge", is_flag=True, default=False)
@click.option("--notify-telegram", is_flag=True, default=False)
@click.option("--dev-branch", help="Target repository development branch override.")
def run(
    project_path: Path,
    task: str,
    auto_merge: bool,
    notify_telegram: bool,
    dev_branch: str | None,
) -> None:
    """Run a HOCA task against a target repository."""
    project_path = require_target_repo(project_path)
    args = [str(project_path), task]
    if auto_merge:
        args.append("--auto-merge")
    if notify_telegram:
        args.append("--notify-telegram")
    if dev_branch:
        args.extend(["--dev-branch", dev_branch])
    run_script("run-hoca-task.sh", args)


@main.command()
@click.argument("project_path", type=click.Path(path_type=Path))
@click.argument("issue_id")
@click.argument("issue_title")
@click.option("--auto-merge", is_flag=True, default=False)
@click.option("--notify-telegram", is_flag=True, default=False)
@click.option("--dev-branch", help="Target repository development branch override.")
def issue(
    project_path: Path,
    issue_id: str,
    issue_title: str,
    auto_merge: bool,
    notify_telegram: bool,
    dev_branch: str | None,
) -> None:
    """Run a HOCA task for a GitHub issue."""
    project_path = require_target_repo(project_path)
    task = f"Fix GitHub issue #{issue_id}: {issue_title}"
    args = [str(project_path), task, "--issue-id", issue_id]
    if auto_merge:
        args.append("--auto-merge")
    if notify_telegram:
        args.append("--notify-telegram")
    if dev_branch:
        args.extend(["--dev-branch", dev_branch])
    run_script("run-hoca-task.sh", args)


@main.command()
@click.argument("project_path", type=click.Path(path_type=Path))
@click.argument("run_id")
@click.option(
    "--regenerate", is_flag=True, default=False, help="Regenerate the report from run artifacts."
)
def report(project_path: Path, run_id: str, regenerate: bool) -> None:
    """Show or regenerate the task report for a past HOCA run."""
    from hoca.run_state import resolve_run_dir
    from hoca.task_report import build_task_report_markdown

    project_path = require_target_repo(project_path)
    run_dir = resolve_run_dir(project_path, run_id)

    if run_dir is None:
        raise click.ClickException(
            f"Run directory not found for {run_id} (checked .hoca-runtime and runtime archive)"
        )

    report_path = run_dir / "task-report.md"

    if regenerate or not report_path.is_file():
        content = build_task_report_markdown(project_path, run_dir)
        report_path.write_text(content, encoding="utf-8")
        click.echo(f"Report regenerated: {report_path}")
    else:
        click.echo(f"Report: {report_path}")


@main.command("kanban-init")
@click.argument("project_path", type=click.Path(path_type=Path))
def kanban_init(project_path: Path) -> None:
    """[Experimental] Initialize a HOCA Kanban board for a target repository."""
    project_path = require_target_repo(project_path)
    run_script("kanban-init.sh", [str(project_path)])


@main.command("kanban-run")
@click.argument("project_path", type=click.Path(path_type=Path))
@click.argument("task")
def kanban_run(project_path: Path, task: str) -> None:
    """[Experimental] Create a HOCA task on the Kanban board for a target repository."""
    project_path = require_target_repo(project_path)
    run_script("kanban-run.sh", [str(project_path), task])


@main.command("kanban-watch")
@click.argument("project_path", type=click.Path(path_type=Path))
def kanban_watch(project_path: Path) -> None:
    """[Experimental] Show the HOCA Kanban board status for a target repository."""
    project_path = require_target_repo(project_path)
    run_script("kanban-watch.sh", [str(project_path)])


@main.group()
def lane() -> None:
    """Manage and communicate with HOCA lanes."""


@lane.command("send")
@click.argument("lane_id")
@click.argument("message")
@click.option("--dry-run", is_flag=True, default=False, help="Plan send without dispatching to tmux.")
def lane_send(lane_id: str, message: str, dry_run: bool) -> None:
    """Send a manager-approved redirection to a lane session."""
    _send_to_lane(lane_id, message, dry_run=dry_run)
    if dry_run:
        click.echo(f"Dry run: not sent lane send to {lane_id}")
    else:
        click.echo(f"Message sent to lane: {lane_id}")


if __name__ == "__main__":
    main()
