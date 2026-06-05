from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoca import kanban_bridge
from hoca.fleet_contracts import HocaFleetTask, HocaLane
from hoca.kanban_bridge import (
    build_kanban_markers,
    create_parent_card,
    map_lane_to_kanban_comment,
    map_task_to_kanban_payload,
    read_run_detail,
    read_worker_status,
    sync_lane_to_kanban,
)


def test_board_name_and_payload_mapping() -> None:
    task = HocaFleetTask(
        task_id="task-1",
        project_id="project-1",
        title="Add login flow",
        status="queued",
        readiness="not_ready",
    )

    payload = map_task_to_kanban_payload(
        task, board_name="hoca:todo-list-repo", workspace="runtime"
    )
    assert payload["title"] == "HOCA: Add login flow"
    assert payload["board"] == "hoca:todo-list-repo"
    assert "task_id=task-1" in payload["body"]

    markers = build_kanban_markers(
        lane_id="lane-1",
        project_id="project-1",
        round_number=2,
        pr_url="https://example.com/pr/1",
        decision="review",
        validation="ok",
        escalation_reason="none",
        artifact_paths=("artifacts/a.txt", "artifacts/b.txt"),
    )
    lane = HocaLane(
        lane_id="lane-1",
        task_id="task-1",
        project_id="project-1",
        status="running",
        attempt_number=1,
    )
    comment = map_lane_to_kanban_comment(lane, markers=markers)

    assert "[spec]" in comment
    assert "[artifact]" in comment
    assert "[validation]" in comment
    assert "[decision]" in comment
    assert "[round]" in comment
    assert "[escalation]" in comment
    assert "[pr]" in comment


def test_create_parent_card_parses_hermes_response(tmp_path, monkeypatch) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()

    task = HocaFleetTask(
        task_id="task-3",
        project_id="project-3",
        title="Create bridge",
        status="queued",
        readiness="not_ready",
    )

    calls: list[list[str]] = []

    def fake_run_hermes_command(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        return 0, '{"id": "card-9"}', ""

    monkeypatch.delenv("HOCA_KANBAN_DISABLED", raising=False)
    monkeypatch.setattr(kanban_bridge, "_run_hermes_command", fake_run_hermes_command)

    assert create_parent_card(task, project_path) == "card-9"
    assert calls and calls[0][0] == "hermes"


def test_create_parent_card_invalid_json_returns_none(tmp_path, monkeypatch) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()

    task = HocaFleetTask(
        task_id="task-4",
        project_id="project-4",
        title="Noisy parsing",
        status="queued",
        readiness="not_ready",
    )

    def fake_run_hermes_command(command: list[str]) -> tuple[int, str, str]:
        assert "create" in command
        return 0, "not-json", "bad response"

    monkeypatch.delenv("HOCA_KANBAN_DISABLED", raising=False)
    monkeypatch.setattr(kanban_bridge, "_run_hermes_command", fake_run_hermes_command)

    assert create_parent_card(task, project_path) is None


def test_disabled_mode_blocks_parent_card_creation(tmp_path, monkeypatch) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    task = HocaFleetTask(
        task_id="task-2", project_id="p2", title="No-op", status="queued", readiness="not_ready"
    )
    monkeypatch.setenv("HOCA_KANBAN_DISABLED", "true")

    assert create_parent_card(task, project_path) is None


def test_sync_laneto_kanban_requires_board(monkeypatch) -> None:
    lane = HocaLane(
        lane_id="lane-1", task_id="task-1", project_id="p1", status="running", attempt_number=1
    )
    assert sync_lane_to_kanban(lane, parent_card_id="") is False

    def unreachable_fetch(*_args: object, **_kwargs: object) -> None:
        pytest.fail("_fetch_json should not be called")

    monkeypatch.setattr(kanban_bridge, "_fetch_json", unreachable_fetch)

    assert read_worker_status(lane_id="lane-1", project_path=Path("/tmp")) is None
    assert read_run_detail(run_id="run-1", project_path=Path("/tmp")) is None


def test_read_endpoints_use_bridge_payload(monkeypatch, tmp_path) -> None:
    def fake_fetch(url: str, timeout: float = 3.0):
        if "workers" in url:
            return {"workers": ["lane-1"]}
        if "runs/run-1" in url:
            return {"state": "complete"}
        return None

    monkeypatch.setenv("HOCA_HERMES_API", "https://example.test/api")
    monkeypatch.setattr(kanban_bridge, "_fetch_json", fake_fetch)

    worker = read_worker_status(lane_id="lane-1", project_path=tmp_path)
    run_detail = read_run_detail(run_id="run-1", project_path=tmp_path)
    assert worker == {"workers": ["lane-1"]}
    assert run_detail == {"state": "complete"}


def test_read_endpoints_fallback_to_cli_when_api_is_unavailable(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_fetch(url: str, timeout: float = 3.0):
        return None

    def fake_run(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        payload = [
            {"lane_id": "lane-7", "id": "lane-7", "state": "running"},
            {"id": "run-9", "state": "complete"},
        ]
        return 0, json.dumps(payload), ""

    monkeypatch.setenv("HOCA_HERMES_API", "https://example.test/api")
    monkeypatch.setattr(kanban_bridge, "_fetch_json", fake_fetch)
    monkeypatch.setattr(kanban_bridge, "_run_hermes_command", fake_run)

    worker = read_worker_status(lane_id="lane-7", project_path=tmp_path)
    run_detail = read_run_detail(run_id="run-9", project_path=tmp_path)

    assert worker == {"lane_id": "lane-7", "id": "lane-7", "state": "running"}
    assert run_detail == {"id": "run-9", "state": "complete"}
    assert calls and calls[0] == calls[1]
