from __future__ import annotations

from pathlib import Path

import json
from subprocess import CompletedProcess

import pytest

import hoca.fleet_monitor as fleet_monitor

from hoca.fleet_monitor import monitor_lane, missing_artifact_reason


def test_monitor_lane_classifies_running_and_reads_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "running", "task": "Build", "project_id": "project-a"}),
        encoding="utf-8",
    )

    snapshot = monitor_lane("lane-1", run_dir, terminal_alive=True)

    assert snapshot.state == "running"
    assert snapshot.status == "running"
    assert snapshot.project_id == "project-a"
    assert snapshot.lane_id == "lane-1"
    assert snapshot.has_validation_artifacts is False
    assert snapshot.has_review_artifacts is False
    assert snapshot.should_process is True


def test_monitor_lane_detects_ready_for_human_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "needs_human_staging", "pr_url": "https://example.test/pr/1"}),
        encoding="utf-8",
    )
    (run_dir / "tests-summary.md").write_text("passed", encoding="utf-8")
    (run_dir / "openhands-review.txt").write_text("ok", encoding="utf-8")
    monkeypatch.setattr("hoca.fleet_monitor._pr_check", lambda pr_url: "passed")

    snapshot = monitor_lane("lane-2", run_dir, terminal_alive=True)

    assert snapshot.state == "ready_for_human"
    assert snapshot.has_validation_artifacts is True
    assert snapshot.has_review_artifacts is True
    assert snapshot.pr_check == "passed"
    assert snapshot.should_process is True


def test_monitor_lane_missing_artifacts_is_stable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    first = monitor_lane("lane-3", run_dir, terminal_alive=False)
    assert first.state == "missing_artifacts"

    second = monitor_lane("lane-3", run_dir, terminal_alive=False)
    assert second.should_process is False
    assert second.state == "missing_artifacts:stabilized"


def test_missing_artifact_reason_reports_blocking_reason(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "failed", "reason": "build failed"}), encoding="utf-8"
    )

    assert missing_artifact_reason(run_dir) == "build failed"


def test_pr_check_classifies_failed_checks_from_github_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "needs_human_staging", "pr_url": "https://example.test/pr/1"}),
        encoding="utf-8",
    )

    checks_output = json.dumps(
        [
            {"name": "ci", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "failure"},
        ]
    )

    def fake_run(command, **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, checks_output, "")

    monkeypatch.setattr(fleet_monitor.subprocess, "run", fake_run)
    snapshot = monitor_lane("lane-4", run_dir, terminal_alive=True)
    assert snapshot.pr_check == "failed"


def test_pr_check_classifies_pending_github_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "running", "pr_url": "https://example.test/pr/2"}),
        encoding="utf-8",
    )

    checks_output = json.dumps(
        [
            {"name": "ci", "status": "in_progress", "conclusion": "neutral"},
            {"name": "lint", "status": "completed", "conclusion": "neutral"},
        ]
    )

    def fake_run(command, **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, checks_output, "")

    monkeypatch.setattr(fleet_monitor.subprocess, "run", fake_run)
    snapshot = monitor_lane("lane-5", run_dir, terminal_alive=True)
    assert snapshot.pr_check == "running"


def test_pr_check_returns_unknown_when_github_check_command_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "running", "pr_url": "https://example.test/pr/3"}),
        encoding="utf-8",
    )

    def fake_run(command, **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 1, "", "not authenticated")

    monkeypatch.setattr(fleet_monitor.subprocess, "run", fake_run)
    snapshot = monitor_lane("lane-6", run_dir, terminal_alive=True)
    assert snapshot.pr_check == "unknown"


def test_monitor_lane_uses_git_checks_before_pr_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path
    (root / ".git").mkdir()
    run_dir = root / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "needs_human_staging",
                "pr_url": "https://example.test/pr/4",
                "base_ref": "main",
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, ...]] = []
    checks_output = json.dumps(
        [
            {"name": "ci", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ]
    )

    def fake_run_command(
        command: list[str], *, cwd: Path | None = None
    ) -> CompletedProcess[str] | None:
        calls.append(tuple(command))
        if command[:3] == ["git", "status", "--short"]:
            return CompletedProcess(command, 0, " M app.py\n?? notes.txt\n", "")
        if command[:2] == ["git", "merge-base"]:
            return CompletedProcess(command, 0, "", "")
        if command[:2] == ["git", "diff"] and "--name-only" in command:
            return CompletedProcess(command, 0, "app.py\nnotes.txt\n", "")
        if command[:3] == ["gh", "pr", "checks"]:
            return CompletedProcess(command, 0, checks_output, "")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(fleet_monitor, "_run_command", fake_run_command)

    snapshot = monitor_lane("lane-7", run_dir, terminal_alive=True)

    assert snapshot.git_changed_files == 2
    assert snapshot.git_diff_files == 2
    assert snapshot.git_merge_base_ok is True
    assert snapshot.pr_check == "passed"
    assert calls[:4] == [
        ("git", "status", "--short"),
        ("git", "merge-base", "--is-ancestor", "main", "HEAD"),
        ("git", "diff", "--name-only", "main...HEAD"),
        ("gh", "pr", "checks", "https://example.test/pr/4", "--json", "conclusion,status,name"),
    ]


def test_monitor_lane_includes_active_hermes_worker_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(
        json.dumps({"status": "running", "project_path": str(tmp_path)}),
        encoding="utf-8",
    )

    payload = {"lane_id": "lane-7", "state": "running"}

    def fake_read_worker_status(*, lane_id: str, project_path: Path) -> dict[str, object]:
        assert lane_id == "lane-7"
        assert project_path == tmp_path.resolve()
        return payload

    monkeypatch.setattr(fleet_monitor, "read_worker_status", fake_read_worker_status)
    snapshot = monitor_lane("lane-7", run_dir, terminal_alive=True, project_path=tmp_path)
    assert snapshot.hermes_worker == payload
