from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from hoca.cli_worker_adapters import (
    CliWorkerAdapterUnavailable,
    run_claude_worker,
    run_codex_worker,
)
from hoca.run_layout import ensure_run_layout
from tests.test_worker_direct import init_repo, sample_task_spec


def make_fake_cli(tmp_path: Path, name: str, body: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    binary = fake_bin / name
    binary.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return fake_bin


def prepare_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    init_repo(project)
    run_dir = project / ".hoca-runtime" / "runs" / "run-test"
    ensure_run_layout(run_dir)
    task_spec_path = run_dir / "task-spec.json"
    task_spec_path.write_text(sample_task_spec(repo_root=str(project)).to_json(), encoding="utf-8")
    return project, run_dir, task_spec_path


def test_run_claude_worker_records_standard_attempt(tmp_path: Path, monkeypatch) -> None:
    project, run_dir, task_spec_path = prepare_run(tmp_path)
    fake_bin = make_fake_cli(
        tmp_path,
        "claude",
        "printf 'updated by claude\\n' > README.md\n"
        "echo 'Claude completed implementation.'\n",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOCA_OPENHANDS_STALL", "2")

    result = run_claude_worker(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.mode == "claude-code"
    assert result.exit_code == 0
    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report["mode"] == "claude-code"
    assert report["commands_run"] == ["claude-code"]
    assert "README.md" in report["changed_files"]


def test_run_claude_worker_missing_cli_fails_cleanly(tmp_path: Path, monkeypatch) -> None:
    project, run_dir, task_spec_path = prepare_run(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(CliWorkerAdapterUnavailable, match="claude CLI"):
        run_claude_worker(
            project_path=project,
            task_spec_path=task_spec_path,
            run_dir=run_dir,
            round_number=1,
        )


def test_run_claude_worker_monitor_blocks_git_lifecycle_output(tmp_path: Path, monkeypatch) -> None:
    project, run_dir, task_spec_path = prepare_run(tmp_path)
    fake_bin = make_fake_cli(tmp_path, "claude", "echo 'git commit -m bad'\n")
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOCA_OPENHANDS_STALL", "2")

    result = run_claude_worker(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.exit_code != 0
    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["blocked_reason"] == "manager_only_git_lifecycle"


def test_run_codex_worker_records_standard_attempt(tmp_path: Path, monkeypatch) -> None:
    project, run_dir, task_spec_path = prepare_run(tmp_path)
    fake_bin = make_fake_cli(
        tmp_path,
        "codex",
        "[[ \"$*\" == *'--sandbox workspace-write'* ]] || { echo 'missing workspace-write' >&2; exit 2; }\n"
        "printf 'updated by codex\\n' > README.md\n"
        "echo 'Codex completed implementation.'\n",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOCA_OPENHANDS_STALL", "2")

    result = run_codex_worker(
        project_path=project,
        task_spec_path=task_spec_path,
        run_dir=run_dir,
        round_number=1,
    )

    assert result.mode == "codex"
    assert result.exit_code == 0
    report = json.loads(result.worker_attempt_path.read_text(encoding="utf-8"))
    assert report["mode"] == "codex"
    assert report["commands_run"] == ["codex"]
    assert "README.md" in report["changed_files"]
    assert "Codex completed implementation." in (run_dir / "openhands-output.log").read_text(
        encoding="utf-8"
    )


def test_run_codex_worker_missing_cli_fails_cleanly(tmp_path: Path, monkeypatch) -> None:
    project, run_dir, task_spec_path = prepare_run(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(CliWorkerAdapterUnavailable, match="codex CLI"):
        run_codex_worker(
            project_path=project,
            task_spec_path=task_spec_path,
            run_dir=run_dir,
            round_number=1,
        )
