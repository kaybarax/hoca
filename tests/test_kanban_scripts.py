from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
KANBAN_INIT_SCRIPT = REPO_ROOT / "scripts" / "kanban-init.sh"
KANBAN_RUN_SCRIPT = REPO_ROOT / "scripts" / "kanban-run.sh"
KANBAN_WATCH_SCRIPT = REPO_ROOT / "scripts" / "kanban-watch.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_kanban_run_creates_parent_task_contract(tmp_path: Path) -> None:
    project = tmp_path / "Todo List Repo"
    fake_bin = tmp_path / "bin"
    log_path = tmp_path / "hermes-args.log"
    project.mkdir()
    fake_bin.mkdir()
    init_repo(project)

    write_executable(
        fake_bin / "hermes",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >> {log_path}
printf '\\n' >> {log_path}
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "-h" ]; then
  exit 0
fi
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "--board" ] && [ "${{4:-}}" = "create" ]; then
  printf '{{"id":"task-123"}}\\n'
  exit 0
fi
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "--board" ] && [ "${{4:-}}" = "comment" ]; then
  exit 0
fi
echo "unexpected hermes args: $*" >&2
exit 2
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(KANBAN_RUN_SCRIPT), str(project), "Add login feature"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Kanban task created: task-123" in result.stdout
    assert "Board: hoca:todo-list-repo" in result.stdout
    assert "Run ID: hoca-" in result.stdout

    args_log = log_path.read_text(encoding="utf-8")
    assert "--board hoca:todo-list-repo create HOCA:\\ Add\\ login\\ feature" in args_log
    assert "--assignee hoca-manager" in args_log
    assert "--workspace" in args_log
    assert "--triage" in args_log
    assert "--idempotency-key hoca-" in args_log
    assert "--skill hoca-manager" in args_log
    assert "HOCA Kanban Parent Task Contract" in args_log
    assert "attempts/worker-attempt-<round>.json" in args_log
    assert "validation/validation-report-<round>.json" in args_log
    assert "reviews/review-report-<round>.json" in args_log
    assert "decisions/manager-decision-<round>.json" in args_log
    assert "Do not rely on private shared memory" in args_log
    assert "queued\\ for\\ triage" in args_log


def test_kanban_init_creates_repo_board_with_current_hermes_cli(tmp_path: Path) -> None:
    project = tmp_path / "Todo List Repo"
    fake_bin = tmp_path / "bin"
    log_path = tmp_path / "hermes-args.log"
    project.mkdir()
    fake_bin.mkdir()
    init_repo(project)

    write_executable(
        fake_bin / "hermes",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >> {log_path}
printf '\\n' >> {log_path}
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "-h" ]; then
  exit 0
fi
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "boards" ] && [ "${{3:-}}" = "create" ]; then
  exit 0
fi
echo "unexpected hermes args: $*" >&2
exit 2
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(KANBAN_INIT_SCRIPT), str(project)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "HOCA Kanban board initialized: hoca:todo-list-repo" in result.stdout
    args_log = log_path.read_text(encoding="utf-8")
    assert "kanban boards create hoca:todo-list-repo" in args_log
    assert "--name HOCA:\\ todo-list-repo" in args_log
    assert "--description HOCA\\ engineering\\ pipeline\\ for\\ todo-list-repo" in args_log


def test_kanban_watch_lists_repo_board_with_current_hermes_cli(tmp_path: Path) -> None:
    project = tmp_path / "Todo List Repo"
    fake_bin = tmp_path / "bin"
    log_path = tmp_path / "hermes-args.log"
    project.mkdir()
    fake_bin.mkdir()
    init_repo(project)

    write_executable(
        fake_bin / "hermes",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >> {log_path}
printf '\\n' >> {log_path}
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "-h" ]; then
  exit 0
fi
if [ "$1" = "kanban" ] && [ "${{2:-}}" = "--board" ] && [ "${{4:-}}" = "list" ]; then
  echo "task list"
  exit 0
fi
echo "unexpected hermes args: $*" >&2
exit 2
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(KANBAN_WATCH_SCRIPT), str(project)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "HOCA Kanban Board: hoca:todo-list-repo" in result.stdout
    assert "task list" in result.stdout
    args_log = log_path.read_text(encoding="utf-8")
    assert "kanban --board hoca:todo-list-repo list" in args_log
