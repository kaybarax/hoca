from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-hoca-task.sh"


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=path, check=True)
    (path / ".gitignore").write_text(".hoca-runtime/\n", encoding="utf-8")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", ".gitignore", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.PIPE)


def run_hoca_task(repo: Path, task: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(repo), task],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_run_hoca_task_reports_workspace_validation_before_dirty_stop(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("human edit\n", encoding="utf-8")

    result = run_hoca_task(tmp_path, "Update README")

    assert result.returncode != 0
    assert "Validating target repository..." in result.stdout
    assert f"Repository root: {tmp_path}" in result.stdout
    assert "Current branch:" in result.stdout
    assert "Working tree status before run:" in result.stdout
    assert "README.md" in result.stdout
    assert "Stopping to avoid mixing unrelated human changes" in result.stdout
