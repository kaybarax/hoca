from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from hoca.cli import main
from hoca.timing_report import build_timing_trends_report


def _seed_repo(tmp_path: Path, name: str = "project") -> Path:
    repo = tmp_path / name
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


def _write_timing_run(
    archive_root: Path,
    repo_name: str,
    run_id: str,
    *,
    wall_time: float,
    agent_loops: int,
    container_starts: int,
    dependency_installs: int,
    mtime: float,
) -> Path:
    run_dir = archive_root / repo_name / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "timings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "counters": {
                    "agent_loops": agent_loops,
                    "container_starts": container_starts,
                    "dependency_installs": dependency_installs,
                },
                "phases": [
                    {
                        "name": "worker_attempt",
                        "started_at": "2026-06-10T00:00:00.000000Z",
                        "duration_seconds": wall_time,
                        "status": "completed",
                        "round": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps({"status": "finished", "reason": ""}),
        encoding="utf-8",
    )
    (run_dir / "final-state.json").write_text(
        json.dumps({"status": "complete"}),
        encoding="utf-8",
    )
    os.utime(run_dir, (mtime, mtime))
    return run_dir


def test_build_timing_trends_report_summarizes_archived_runs(tmp_path: Path, monkeypatch) -> None:
    repo = _seed_repo(tmp_path, "trend-repo")
    archive_root = tmp_path / "archives"
    monkeypatch.setenv("HOCA_RUNTIME_ARCHIVE_ROOT", str(archive_root))

    _write_timing_run(
        archive_root,
        repo.name,
        "run-1",
        wall_time=1.5,
        agent_loops=2,
        container_starts=1,
        dependency_installs=3,
        mtime=1000.0,
    )
    _write_timing_run(
        archive_root,
        repo.name,
        "run-2",
        wall_time=3.0,
        agent_loops=4,
        container_starts=2,
        dependency_installs=1,
        mtime=2000.0,
    )

    report = build_timing_trends_report(repo)

    assert "HOCA Timing Trend Report" in report
    assert f"Project: {repo.name}" in report
    assert "Archived runs: 2" in report
    assert "Wall time median: 2.25s (min 1.50s, max 3.00s)" in report
    assert "run-1\t1.50s\t-\t1\t2\t1\t3\tfinished\tcomplete" in report
    assert "run-2\t3.00s\t+1.50s\t1\t4\t2\t1\tfinished\tcomplete" in report


def test_report_command_prints_timing_trends_for_archived_runs(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path, "cli-trend-repo")
    archive_root = tmp_path / "archives"

    _write_timing_run(
        archive_root,
        repo.name,
        "run-1",
        wall_time=2.0,
        agent_loops=1,
        container_starts=1,
        dependency_installs=0,
        mtime=1000.0,
    )

    result = CliRunner().invoke(
        main,
        ["report", str(repo), "--timing-trends"],
        env={"HOCA_RUNTIME_ARCHIVE_ROOT": str(archive_root)},
    )

    assert result.exit_code == 0
    assert "HOCA Timing Trend Report" in result.output
    assert "Archived runs: 1" in result.output
    assert "run-1\t2.00s\t-\t1\t1\t1\t0\tfinished\tcomplete" in result.output
