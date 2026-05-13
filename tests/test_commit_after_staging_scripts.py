from __future__ import annotations

import subprocess
from pathlib import Path

from tests.test_safe_staging_scripts import init_repo, run_safe_stage, write_run_files

COMMIT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "commit-after-staging.sh"


def run_commit(
    repo: Path, task: str, run_dir: Path, *, issue_id: str | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = [str(COMMIT_SCRIPT), str(repo), task, str(run_dir)]
    if issue_id:
        cmd.extend(["--issue-id", issue_id])
    return subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_commit_requires_matching_staged_files_list(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    assert run_safe_stage(tmp_path, "Update README", run_dir).returncode == 0

    (run_dir / "staged-files.txt").write_text("README.md\nextra.txt\n", encoding="utf-8")

    result = run_commit(tmp_path, "Update README", run_dir)

    assert result.returncode != 0
    assert "staged-files.txt must match" in result.stderr


def test_commit_refuses_task_that_looks_like_secrets(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    assert run_safe_stage(tmp_path, "Update README", run_dir).returncode == 0

    result = run_commit(tmp_path, "Rotate api_key for service", run_dir)

    assert result.returncode != 0
    assert "secrets" in result.stderr


def test_commit_creates_commit_and_records_hash(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    assert run_safe_stage(tmp_path, "Document billing in README", run_dir).returncode == 0

    result = run_commit(tmp_path, "Document billing in README", run_dir)

    assert result.returncode == 0, result.stderr
    assert (run_dir / "commit-hash.txt").is_file()
    assert (run_dir / "commit-message.txt").read_text(encoding="utf-8").strip().startswith("docs:")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    assert (run_dir / "commit-hash.txt").read_text(encoding="utf-8").strip() == head


def test_commit_includes_issue_id_in_subject(tmp_path: Path) -> None:
    init_repo(tmp_path)
    run_dir = tmp_path / ".hoca-runtime" / "runs" / "run-1"
    write_run_files(run_dir)
    (run_dir / "intended-files.txt").write_text("README.md\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    assert run_safe_stage(tmp_path, "Update README", run_dir).returncode == 0

    result = run_commit(tmp_path, "Update README", run_dir, issue_id="42")

    assert result.returncode == 0, result.stderr
    msg = (run_dir / "commit-message.txt").read_text(encoding="utf-8").strip()
    assert "(#42)" in msg
