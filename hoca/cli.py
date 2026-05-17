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


@main.command()
@click.argument("project_path", type=click.Path(path_type=Path))
@click.argument("task")
@click.option("--auto-merge", is_flag=True, default=False)
@click.option("--notify-telegram", is_flag=True, default=False)
@click.option("--model", help="Ollama model alias to use for this run, for example qwen-14b-pro.")
def run(
    project_path: Path,
    task: str,
    auto_merge: bool,
    notify_telegram: bool,
    model: str | None,
) -> None:
    """Run a HOCA task against a target repository."""
    project_path = require_target_repo(project_path)
    args = [str(project_path), task]
    if auto_merge:
        args.append("--auto-merge")
    if notify_telegram:
        args.append("--notify-telegram")
    if model:
        args.extend(["--model", model])
    run_script("run-hoca-task.sh", args)


@main.command()
@click.argument("project_path", type=click.Path(path_type=Path))
@click.argument("issue_id")
@click.argument("issue_title")
@click.option("--auto-merge", is_flag=True, default=False)
@click.option("--notify-telegram", is_flag=True, default=False)
@click.option("--model", help="Ollama model alias to use for this run, for example qwen-14b-pro.")
def issue(
    project_path: Path,
    issue_id: str,
    issue_title: str,
    auto_merge: bool,
    notify_telegram: bool,
    model: str | None,
) -> None:
    """Run a HOCA task for a GitHub issue."""
    project_path = require_target_repo(project_path)
    task = f"Fix GitHub issue #{issue_id}: {issue_title}"
    args = [str(project_path), task, "--issue-id", issue_id]
    if auto_merge:
        args.append("--auto-merge")
    if notify_telegram:
        args.append("--notify-telegram")
    if model:
        args.extend(["--model", model])
    run_script("run-hoca-task.sh", args)


if __name__ == "__main__":
    main()
