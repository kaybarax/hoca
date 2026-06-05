from __future__ import annotations

from dataclasses import replace
import re
import subprocess
from pathlib import Path
import time

import click

from hoca.doctor import run_doctor
from hoca.fleet_contracts import (
    HocaFleetTask,
    HocaLane,
    HocaProject,
    HocaResourceBudget,
    HocaSchedulerDecision,
)
from hoca.fleet_registry import FleetRegistry
from hoca.paths import repo_root
from hoca.resource_governor import ResourceGovernor
from hoca.scheduler import FleetScheduler, run_scheduler_loop
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
    return any(
        token in lowered
        for token in ("api_key", "api-key", "secret", "token", "password", "private_key")
    )


def _append_send_log(run_dir: Path, lane_id: str, message: str, *, dry_run: bool) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "lane-send.log"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tag = "dry-run" if dry_run else "sent"
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} {lane_id} {tag}={message}\n")


def _resolve_lane_for_send(
    lane_id: str, *, control_root: Path | None = None
) -> tuple[HocaLane, Path]:
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


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sanitize_project_id(value: str) -> str:
    project_id = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return project_id or "project"


def _default_project_id(project_path: Path) -> str:
    return _sanitize_project_id(project_path.name)


def _default_task_id(title: str) -> str:
    return _sanitize_project_id(title)


def _registry() -> FleetRegistry:
    return FleetRegistry()


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


@main.group()
def project() -> None:
    """Manage registered HOCA projects."""


@project.command("add")
@click.argument("project_path", type=click.Path(path_type=Path))
@click.option("--project-id", help="Explicit project identifier.")
@click.option("--name", "display_name", help="Human-friendly project name.")
@click.option(
    "--default-branch",
    default="main",
    show_default=True,
    help="Default branch used for the project registry entry.",
)
@click.option(
    "--max-parallel-tasks",
    default=1,
    type=click.IntRange(min=1),
    show_default=True,
    help="Maximum concurrent tasks for the registered project.",
)
def project_add(
    project_path: Path,
    project_id: str | None,
    display_name: str | None,
    default_branch: str,
    max_parallel_tasks: int,
) -> None:
    """Register a Git repository as a HOCA project."""
    project_path = require_target_repo(project_path)
    resolved_project_id = _sanitize_project_id(project_id or _default_project_id(project_path))
    timestamp = _utc_now()
    project = HocaProject(
        project_id=resolved_project_id,
        repo_path=str(project_path),
        display_name=display_name or project_path.name,
        default_branch=default_branch,
        max_parallel_tasks=max_parallel_tasks,
        created_at=timestamp,
        updated_at=timestamp,
        is_active=True,
    )

    try:
        _registry().create_project(project)
    except ValueError as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Project added: {resolved_project_id}")


@project.command("list")
def project_list() -> None:
    """List registered HOCA projects."""
    projects = sorted(_registry().list_projects(), key=lambda project: project.project_id)
    if not projects:
        click.echo("No projects registered.")
        return

    click.echo("PROJECT_ID\tDISPLAY_NAME\tREPO_PATH\tACTIVE")
    for project in projects:
        click.echo(
            "\t".join(
                (
                    project.project_id,
                    project.display_name or project.project_id,
                    project.repo_path,
                    "yes" if project.is_active else "no",
                )
            )
        )


@project.command("show")
@click.argument("project_id")
def project_show(project_id: str) -> None:
    """Show a registered HOCA project."""
    project = _registry().get_project(project_id)
    if project is None:
        raise click.ClickException(f"Project not found: {project_id}")

    click.echo(f"Project ID: {project.project_id}")
    click.echo(f"Display Name: {project.display_name or project.project_id}")
    click.echo(f"Repository: {project.repo_path}")
    click.echo(f"Default Branch: {project.default_branch}")
    click.echo(f"Max Parallel Tasks: {project.max_parallel_tasks}")
    click.echo(f"Active: {'yes' if project.is_active else 'no'}")
    if project.runtime_archive_root:
        click.echo(f"Runtime Archive Root: {project.runtime_archive_root}")


@project.command("doctor")
def project_doctor() -> None:
    """Check registered HOCA projects for local repository health."""
    projects = _registry().list_projects()
    if not projects:
        click.echo("No projects registered.")
        return

    failures: list[str] = []
    for project in projects:
        try:
            require_target_repo(Path(project.repo_path))
        except click.ClickException as error:
            failures.append(f"{project.project_id}: {error}")

    if failures:
        for failure in failures:
            click.echo(failure)
        raise click.ClickException(f"Project doctor found {len(failures)} critical failure(s).")

    click.echo(f"Project doctor OK for {len(projects)} project(s).")


@project.command("remove")
@click.argument("project_id")
def project_remove(project_id: str) -> None:
    """Remove a registered HOCA project."""
    registry = _registry()
    if registry.get_project(project_id) is None:
        raise click.ClickException(f"Project not found: {project_id}")

    try:
        registry.delete_project(project_id)
    except ValueError as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Project removed: {project_id}")


@main.group()
def task() -> None:
    """Manage HOCA tasks."""


@task.command("create")
@click.argument("project_id")
@click.argument("title")
@click.option("--task-id", help="Explicit task identifier.")
@click.option("--description", default="", help="Optional task description.")
@click.option("--goal", default="", help="Optional task goal.")
@click.option("--issue-id", default=None, help="Optional linked issue identifier.")
@click.option(
    "--dependency",
    "dependencies",
    multiple=True,
    help="Dependency task identifier. May be supplied multiple times.",
)
@click.option(
    "--priority",
    default=1,
    type=click.IntRange(min=0),
    show_default=True,
    help="Task priority.",
)
@click.option(
    "--status",
    default="queued",
    type=click.Choice(["queued", "ready", "running", "blocked", "cancelled", "completed"]),
    show_default=True,
    help="Initial task status.",
)
@click.option(
    "--readiness",
    default="not_ready",
    type=click.Choice(["not_ready", "ready", "draft_ready", "blocked"]),
    show_default=True,
    help="Initial readiness state.",
)
def task_create(
    project_id: str,
    title: str,
    task_id: str | None,
    description: str,
    goal: str,
    issue_id: str | None,
    dependencies: tuple[str, ...],
    priority: int,
    status: str,
    readiness: str,
) -> None:
    """Create a HOCA task for a registered project."""
    registry = _registry()
    if registry.get_project(project_id) is None:
        raise click.ClickException(f"Project not found: {project_id}")

    resolved_task_id = _sanitize_project_id(task_id or _default_task_id(title))
    timestamp = _utc_now()
    task = HocaFleetTask(
        task_id=resolved_task_id,
        project_id=project_id,
        title=title.strip(),
        description=description.strip(),
        issue_id=issue_id,
        goal=goal.strip(),
        status=status,  # type: ignore[arg-type]
        readiness=readiness,  # type: ignore[arg-type]
        dependencies=list(dependencies),
        lane_ids=[],
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=None,
        priority=priority,
        metadata={},
    )

    try:
        registry.create_task(task)
    except ValueError as error:
        raise click.ClickException(str(error)) from error

    click.echo(f"Task created: {resolved_task_id}")


@task.command("list")
@click.option("--project-id", default=None, help="Filter tasks by project identifier.")
@click.option(
    "--status",
    "statuses",
    multiple=True,
    type=click.Choice(["queued", "ready", "running", "blocked", "cancelled", "completed"]),
    help="Filter tasks by status. May be supplied multiple times.",
)
def task_list(project_id: str | None, statuses: tuple[str, ...]) -> None:
    """List HOCA tasks."""
    tasks = sorted(_registry().list_tasks(project_id=project_id), key=lambda task: task.task_id)
    if statuses:
        tasks = [task for task in tasks if task.status in statuses]

    if not tasks:
        click.echo("No tasks found.")
        return

    click.echo("TASK_ID\tPROJECT_ID\tSTATUS\tREADINESS\tTITLE")
    for task in tasks:
        click.echo(
            "\t".join(
                (
                    task.task_id,
                    task.project_id,
                    task.status,
                    task.readiness,
                    task.title or task.task_id,
                )
            )
        )


@task.command("show")
@click.argument("task_id")
def task_show(task_id: str) -> None:
    """Show a HOCA task."""
    task = _registry().get_task(task_id)
    if task is None:
        raise click.ClickException(f"Task not found: {task_id}")

    click.echo(f"Task ID: {task.task_id}")
    click.echo(f"Project ID: {task.project_id}")
    click.echo(f"Title: {task.title}")
    click.echo(f"Status: {task.status}")
    click.echo(f"Readiness: {task.readiness}")
    click.echo(f"Priority: {task.priority}")
    if task.description:
        click.echo(f"Description: {task.description}")
    if task.goal:
        click.echo(f"Goal: {task.goal}")
    if task.issue_id:
        click.echo(f"Issue ID: {task.issue_id}")
    if task.dependencies:
        click.echo(f"Dependencies: {', '.join(task.dependencies)}")
    if task.lane_ids:
        click.echo(f"Lane IDs: {', '.join(task.lane_ids)}")
    click.echo(f"Created At: {task.created_at}")
    click.echo(f"Updated At: {task.updated_at}")


def _update_task_status(
    task_id: str, *, status: str, readiness: str | None = None
) -> HocaFleetTask:
    registry = _registry()
    task = registry.get_task(task_id)
    if task is None:
        raise click.ClickException(f"Task not found: {task_id}")

    next_task = replace(
        task,
        status=status,  # type: ignore[arg-type]
        readiness=readiness or task.readiness,  # type: ignore[arg-type]
        updated_at=_utc_now(),
    )
    try:
        registry.update_task(task_id, next_task)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    return next_task


def _update_lane_status(lane_id: str, *, status: str) -> HocaLane:
    registry = _registry()
    lane = registry.get_lane(lane_id)
    if lane is None:
        raise click.ClickException(f"Lane not found: {lane_id}")

    next_lane = replace(lane, status=status, updated_at=_utc_now())
    try:
        registry.update_lane(lane_id, next_lane)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    return next_lane


@task.command("cancel")
@click.argument("task_id")
def task_cancel(task_id: str) -> None:
    """Cancel a HOCA task."""
    _update_task_status(task_id, status="cancelled")
    click.echo(f"Task cancelled: {task_id}")


@task.command("block")
@click.argument("task_id")
def task_block(task_id: str) -> None:
    """Mark a HOCA task as blocked."""
    _update_task_status(task_id, status="blocked", readiness="blocked")
    click.echo(f"Task blocked: {task_id}")


@main.command("setup-profiles")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Print planned actions without changing files."
)
def setup_profiles(dry_run: bool) -> None:
    """Install or update HOCA Hermes role profiles from repo templates."""
    args = ["--dry-run"] if dry_run else []
    run_script("setup-hermes-profiles.sh", args)


def _default_resource_budget() -> HocaResourceBudget:
    timestamp = _utc_now()
    return HocaResourceBudget(
        budget_id="default",
        max_parallel_projects=999,
        max_parallel_tasks=999,
        max_parallel_lanes=999,
        max_agents=999,
        memory_limit_mb=0,
        cpu_limit_percent=0,
        created_at=timestamp,
        updated_at=timestamp,
        metadata={},
    )


def _default_scheduler() -> FleetScheduler:
    return FleetScheduler(
        registry=_registry(), governor=ResourceGovernor(budget=_default_resource_budget())
    )


def _decision_summary(decision: HocaSchedulerDecision) -> str:
    parts = [decision.decision_type, decision.project_id]
    if decision.task_id:
        parts.append(decision.task_id)
    if decision.lane_id:
        parts.append(decision.lane_id)
    parts.append(decision.reason)
    return "\t".join(parts)


def _fleet_state_summary() -> list[str]:
    registry = _registry()
    projects = registry.list_projects()
    tasks = registry.list_tasks()
    lanes = registry.list_lanes()
    queued_tasks = [task for task in tasks if task.status == "queued"]
    running_lanes = [
        lane
        for lane in lanes
        if lane.status
        in {"allocated", "starting", "running", "validating", "reviewing", "repairing"}
    ]
    blocked_lanes = [lane for lane in lanes if lane.status == "blocked"]
    ready_prs = [lane for lane in lanes if lane.status in {"pr_created", "ready_for_human"}]
    return [
        f"Projects: {len(projects)}",
        f"Queued Tasks: {len(queued_tasks)}",
        f"Running Lanes: {len(running_lanes)}",
        f"Blocked Lanes: {len(blocked_lanes)}",
        f"Ready PRs: {len(ready_prs)}",
    ]


@main.group()
def scheduler() -> None:
    """Manage the HOCA scheduler."""


@scheduler.command("tick")
def scheduler_tick() -> None:
    """Run one scheduler tick."""
    decisions = _default_scheduler().tick()
    if not decisions:
        click.echo("No scheduler decisions.")
        return
    for decision in decisions:
        click.echo(_decision_summary(decision))


@scheduler.command("start")
@click.option(
    "--interval",
    default=0.0,
    type=float,
    show_default=True,
    help="Seconds to sleep between ticks.",
)
@click.option(
    "--iterations",
    default=1,
    type=click.IntRange(min=1),
    show_default=True,
    help="Number of scheduler iterations to run.",
)
def scheduler_start(interval: float, iterations: int) -> None:
    """Start the scheduler loop."""
    scheduler_runner = _default_scheduler()
    iterations_run = run_scheduler_loop(
        scheduler=scheduler_runner,
        interval_seconds=interval,
        max_iterations=iterations,
        read_only_on_conflict=True,
    )
    for iteration, decisions in iterations_run:
        if iteration >= 0:
            click.echo(f"Iteration {iteration}:")
            for decision in decisions:
                click.echo(_decision_summary(decision))
    click.echo(
        f"Scheduler loop finished after {len([iteration for iteration, _ in iterations_run if iteration >= 0])} iteration(s)."
    )


@scheduler.command("status")
def scheduler_status() -> None:
    """Show a scheduler summary."""
    for line in _fleet_state_summary():
        click.echo(line)


def _cleanup_cleaned_lanes(*, dry_run: bool) -> list[str]:
    registry = _registry()
    lanes_index = registry._load_index(registry.paths.lanes_json)
    cleaned_lane_ids = [
        lane_id for lane_id, payload in lanes_index.items() if payload.get("status") == "cleaned"
    ]
    if dry_run or not cleaned_lane_ids:
        return cleaned_lane_ids

    remaining_lanes = {
        lane_id: payload
        for lane_id, payload in lanes_index.items()
        if lane_id not in cleaned_lane_ids
    }
    registry._write_index(registry.paths.lanes_json, remaining_lanes)

    tasks_index = registry._load_index(registry.paths.tasks_json)
    tasks_changed = False
    for task_id, payload in tasks_index.items():
        lane_ids = [
            lane_id
            for lane_id in list(payload.get("lane_ids") or [])
            if lane_id not in cleaned_lane_ids
        ]
        if lane_ids != list(payload.get("lane_ids") or []):
            payload["lane_ids"] = lane_ids
            tasks_index[task_id] = payload
            tasks_changed = True
    if tasks_changed:
        registry._write_index(registry.paths.tasks_json, tasks_index)

    return cleaned_lane_ids


@main.group()
def fleet() -> None:
    """Manage fleet-level HOCA state."""


@fleet.command("status")
def fleet_status() -> None:
    """Show a fleet summary."""
    for line in _fleet_state_summary():
        click.echo(line)


@fleet.command("doctor")
def fleet_doctor() -> None:
    """Check fleet-level registry consistency."""
    registry = _registry()
    projects = registry.list_projects()
    project_ids = {project.project_id for project in projects}
    task_ids = {task.task_id for task in registry.list_tasks()}
    failures: list[str] = []

    for project in projects:
        try:
            require_target_repo(Path(project.repo_path))
        except click.ClickException as error:
            failures.append(f"{project.project_id}: {error}")

    for task in registry.list_tasks():
        if task.project_id not in project_ids:
            failures.append(f"{task.task_id}: unknown project {task.project_id}")

    for lane in registry.list_lanes():
        if lane.project_id not in project_ids:
            failures.append(f"{lane.lane_id}: unknown project {lane.project_id}")
        if lane.task_id not in task_ids:
            failures.append(f"{lane.lane_id}: unknown task {lane.task_id}")

    if failures:
        for failure in failures:
            click.echo(failure)
        raise click.ClickException(f"Fleet doctor found {len(failures)} critical failure(s).")

    click.echo(f"Fleet doctor OK for {len(projects)} project(s).")


@fleet.command("report")
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional report file path.",
)
def fleet_report(output: Path | None) -> None:
    """Write a fleet status report."""
    registry = _registry()
    target = output or (registry.paths.root / "fleet-report.md")
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# HOCA Fleet Report", ""]
    lines.extend(_fleet_state_summary())
    lines.append("")
    lines.append("Projects:")
    for project in sorted(registry.list_projects(), key=lambda item: item.project_id):
        lines.append(f"- {project.project_id} -> {project.repo_path}")
    lines.append("")
    lines.append("Tasks:")
    for task in sorted(registry.list_tasks(), key=lambda item: item.task_id):
        lines.append(f"- {task.task_id} [{task.status}] ({task.project_id})")
    lines.append("")
    lines.append("Lanes:")
    for lane in sorted(registry.list_lanes(), key=lambda item: item.lane_id):
        lines.append(f"- {lane.lane_id} [{lane.status}] ({lane.project_id}/{lane.task_id})")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    click.echo(f"Fleet report written: {target}")


@fleet.command("cleanup")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview cleaned-lane removal without changing files.",
)
def fleet_cleanup(dry_run: bool) -> None:
    """Remove cleaned lanes from the registry."""
    cleaned_lane_ids = _cleanup_cleaned_lanes(dry_run=dry_run)
    if not cleaned_lane_ids:
        click.echo("No cleaned lanes found.")
        return

    for lane_id in cleaned_lane_ids:
        if dry_run:
            click.echo(f"Would remove cleaned lane: {lane_id}")
        else:
            click.echo(f"Removed cleaned lane: {lane_id}")


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


@lane.command("list")
@click.option("--project-id", default=None, help="Filter lanes by project identifier.")
@click.option("--task-id", default=None, help="Filter lanes by task identifier.")
@click.option(
    "--status",
    "statuses",
    multiple=True,
    type=click.Choice(
        [
            "allocated",
            "starting",
            "running",
            "validating",
            "reviewing",
            "repairing",
            "pr_created",
            "ready_for_human",
            "blocked",
            "failed",
            "cleaned",
        ]
    ),
    help="Filter lanes by status. May be supplied multiple times.",
)
def lane_list(project_id: str | None, task_id: str | None, statuses: tuple[str, ...]) -> None:
    """List HOCA lanes."""
    lanes = sorted(
        _registry().list_lanes(task_id=task_id, project_id=project_id),
        key=lambda lane: lane.lane_id,
    )
    if statuses:
        lanes = [lane for lane in lanes if lane.status in statuses]

    if not lanes:
        click.echo("No lanes found.")
        return

    click.echo("LANE_ID\tTASK_ID\tPROJECT_ID\tSTATUS\tBRANCH\tRUN_DIR")
    for lane in lanes:
        click.echo(
            "\t".join(
                (
                    lane.lane_id,
                    lane.task_id,
                    lane.project_id,
                    lane.status,
                    lane.branch,
                    lane.run_dir or "",
                )
            )
        )


@lane.command("show")
@click.argument("lane_id")
def lane_show(lane_id: str) -> None:
    """Show a HOCA lane."""
    registry = _registry()
    lane = registry.get_lane(lane_id)
    if lane is None:
        raise click.ClickException(f"Lane not found: {lane_id}")

    resolved_run_dir = lane.run_dir
    if lane.run_dir:
        raw_run_dir = Path(lane.run_dir)
        if raw_run_dir.is_absolute():
            resolved_run_dir = str(raw_run_dir)
        else:
            project = registry.get_project(lane.project_id)
            if project is not None:
                resolved_run_dir = str(Path(project.repo_path) / raw_run_dir)

    click.echo(f"Lane ID: {lane.lane_id}")
    click.echo(f"Task ID: {lane.task_id}")
    click.echo(f"Project ID: {lane.project_id}")
    click.echo(f"Status: {lane.status}")
    click.echo(f"Branch: {lane.branch}")
    click.echo(f"Adapter: {lane.adapter_id or 'default'}")
    click.echo(f"Run Dir: {resolved_run_dir or '(unset)'}")
    if lane.worktree_path:
        click.echo(f"Worktree: {lane.worktree_path}")
    if lane.session_id:
        click.echo(f"Session ID: {lane.session_id}")
    if lane.run_ref:
        click.echo(f"Run Ref: {lane.run_ref}")
    click.echo(f"Attempt: {lane.attempt_number}")
    click.echo(f"Created At: {lane.created_at}")
    click.echo(f"Updated At: {lane.updated_at}")


@lane.command("logs")
@click.argument("lane_id")
def lane_logs(lane_id: str) -> None:
    """Print known log paths for a HOCA lane."""
    _, run_dir = _resolve_lane_for_send(lane_id)
    click.echo(f"Run Dir: {run_dir}")
    if not run_dir.exists():
        raise click.ClickException(f"Lane run directory not found: {run_dir}")

    files = sorted(path for path in run_dir.rglob("*") if path.is_file())
    if not files:
        click.echo("No log files found.")
        return

    for path in files:
        click.echo(str(path))


@lane.command("stop")
@click.argument("lane_id")
def lane_stop(lane_id: str) -> None:
    """Mark a HOCA lane as cleaned."""
    _update_lane_status(lane_id, status="cleaned")
    click.echo(f"Lane stopped: {lane_id}")


@lane.command("send")
@click.argument("lane_id")
@click.argument("message")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Plan send without dispatching to tmux."
)
def lane_send(lane_id: str, message: str, dry_run: bool) -> None:
    """Send a manager-approved redirection to a lane session."""
    _send_to_lane(lane_id, message, dry_run=dry_run)
    if dry_run:
        click.echo(f"Dry run: not sent lane send to {lane_id}")
    else:
        click.echo(f"Message sent to lane: {lane_id}")


if __name__ == "__main__":
    main()
