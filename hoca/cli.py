from __future__ import annotations

import subprocess
from pathlib import Path

import click

from hoca.doctor import run_doctor
from hoca.paths import repo_root


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
@click.option("--dry-run", is_flag=True, default=False, help="Print planned actions without changing files.")
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
@click.option("--regenerate", is_flag=True, default=False, help="Regenerate the report from run artifacts.")
def report(project_path: Path, run_id: str, regenerate: bool) -> None:
    """Show or regenerate the task report for a past HOCA run."""
    from hoca.run_state import resolve_run_dir
    from hoca.task_report import build_task_report_markdown

    project_path = require_target_repo(project_path)
    run_dir = resolve_run_dir(project_path, run_id)

    if run_dir is None:
        raise click.ClickException(f"Run directory not found for {run_id} (checked .hoca-runtime and runtime archive)")

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


if __name__ == "__main__":
    main()
