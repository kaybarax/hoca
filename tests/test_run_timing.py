from __future__ import annotations

import json
from pathlib import Path

from hoca.run_timing import record_event, record_phase


def test_record_phase_writes_machine_readable_timing(tmp_path: Path) -> None:
    record_phase(
        tmp_path,
        name="worker",
        started_at_epoch=100.0,
        ended_at_epoch=103.25,
        round_number=2,
        role="worker",
        mode="hermes",
        model="worker",
    )

    data = json.loads((tmp_path / "timings.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["run_id"] == tmp_path.name
    assert data["phases"] == [
        {
            "duration_seconds": 3.25,
            "ended_at": "1970-01-01T00:01:43.250000Z",
            "mode": "hermes",
            "model": "worker",
            "name": "worker",
            "role": "worker",
            "round": 2,
            "started_at": "1970-01-01T00:01:40.000000Z",
            "status": "completed",
        }
    ]


def test_record_event_updates_countable_counters(tmp_path: Path) -> None:
    record_event(tmp_path, event_type="container_start", name="docker-run", role="worker")
    record_event(tmp_path, event_type="dependency_install", name="yarn-install")
    record_event(tmp_path, event_type="agent_loop", name="openhands", round_number=1)

    data = json.loads((tmp_path / "timings.json").read_text(encoding="utf-8"))
    assert data["counters"] == {
        "agent_loops": 1,
        "container_starts": 1,
        "dependency_installs": 1,
    }
    assert [event["type"] for event in data["events"]] == [
        "container_start",
        "dependency_install",
        "agent_loop",
    ]
