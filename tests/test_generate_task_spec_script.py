from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate-task-spec.sh"
HOCA_ROOT = SCRIPT.parents[1]


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def run_script(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HOCA_ROOT)
    env["HOCA_PYTHON"] = sys.executable
    return subprocess.run(
        [str(SCRIPT), *args],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        cwd=cwd or HOCA_ROOT,
    )


def test_script_writes_task_spec_json(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-shell"
    run_dir.mkdir(parents=True)

    result = run_script(
        str(tmp_path),
        "Update README",
        str(run_dir),
        "--run-id",
        "run-shell",
        "--base-branch",
        "main",
        "--task-branch",
        "feat/readme",
    )

    assert result.returncode == 0, result.stderr
    task_spec = run_dir / "task-spec.json"
    assert task_spec.is_file()
    data = json.loads(task_spec.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-shell"
    assert data["raw_request"] == "Update README"
    assert (run_dir / "task-spec-context.json").is_file()


def test_script_fails_on_invalid_repo(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "missing-git"
    run_dir.mkdir(parents=True)

    result = run_script(str(tmp_path), "task", str(run_dir))

    assert result.returncode == 1
    assert "Not a Git repository" in result.stderr


def test_script_fails_on_empty_task(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / "runs" / "empty-task"
    run_dir.mkdir(parents=True)

    result = run_script(str(tmp_path), "   ", str(run_dir))

    assert result.returncode == 1
    assert "empty" in result.stderr.lower()


def test_script_documents_required_behavior() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "hoca.task_spec" in script
    assert "--issue-id" in script
    assert "Not a Git repository" in script
