from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore-dev-branch.sh"


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def run_restore(
    repo: Path,
    *extra_args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [str(SCRIPT), str(repo), *extra_args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=run_env,
    )


def test_restore_dev_branch_dry_run_reports_planned_switch(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-b", "feat/task"], cwd=tmp_path, check=True)

    result = run_restore(
        tmp_path,
        "--dev-branch",
        "main",
        "--initial-branch",
        "main",
        "--dry-run",
    )

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0
    assert "dry-run would switch feat/task -> main" in result.stdout
    assert branch == "feat/task"


def test_restore_dev_branch_checks_out_dev_branch(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-b", "fix/issue-42"], cwd=tmp_path, check=True)

    result = run_restore(
        tmp_path,
        "--dev-branch",
        "main",
        "--initial-branch",
        "develop",
    )

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0
    assert "Restoring development branch: main" in result.stdout
    assert branch == "main"


def test_restore_dev_branch_uses_initial_branch_when_dev_unset(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "develop"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-b", "feat/task"], cwd=tmp_path, check=True)

    result = run_restore(tmp_path, "--initial-branch", "develop")

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0
    assert branch == "develop"


def test_restore_dev_branch_skips_uncommitted_changes(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-b", "feat/task"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")

    result = run_restore(
        tmp_path,
        "--dev-branch",
        "main",
        "--initial-branch",
        "main",
    )

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0
    assert "skipped (uncommitted changes" in result.stdout
    assert branch == "feat/task"


def test_restore_dev_branch_ignores_hoca_runtime_changes(tmp_path: Path) -> None:
    init_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-b", "feat/task"], cwd=tmp_path, check=True)
    runtime = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    runtime.mkdir(parents=True)
    (runtime / "status.json").write_text('{"status":"running"}\n', encoding="utf-8")

    result = run_restore(
        tmp_path,
        "--dev-branch",
        "main",
        "--initial-branch",
        "main",
    )

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert result.returncode == 0
    assert branch == "main"
