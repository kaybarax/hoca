from __future__ import annotations

import json
from pathlib import Path

from hoca.timing_report import build_timing_report


def test_build_timing_report_renders_totals_and_phase_table(tmp_path: Path) -> None:
    (tmp_path / "timings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-123",
                "counters": {
                    "agent_loops": 2,
                    "container_starts": 1,
                    "dependency_installs": 3,
                },
                "events": [],
                "phases": [
                    {
                        "name": "review_pass",
                        "started_at": "2026-06-10T00:00:03.000000Z",
                        "ended_at": "2026-06-10T00:00:05.500000Z",
                        "duration_seconds": 2.5,
                        "status": "completed",
                        "round": 1,
                        "role": "reviewer",
                        "mode": "hermes",
                        "model": "reviewer",
                    },
                    {
                        "name": "worker_attempt",
                        "started_at": "2026-06-10T00:00:00.000000Z",
                        "ended_at": "2026-06-10T00:00:03.000000Z",
                        "duration_seconds": 3,
                        "status": "completed",
                        "round": 1,
                        "role": "worker",
                        "mode": "hermes",
                        "model": "worker",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_timing_report(tmp_path)

    assert "Run ID: run-123" in report
    assert "Wall time: 5.50s" in report
    assert "Rounds used: 1" in report
    assert "Agent loops: 2" in report
    assert "Container starts: 1" in report
    assert "Dependency installs: 3" in report
    assert "worker_attempt\t3.00s\tcompleted\t1\tworker\thermes\tworker" in report
    assert "review_pass\t2.50s\tcompleted\t1\treviewer\thermes\treviewer" in report


def test_build_timing_report_renders_failed_and_blocked_runs(tmp_path: Path) -> None:
    (tmp_path / "timings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-blocked",
                "counters": {},
                "events": [],
                "phases": [
                    {
                        "name": "doctor",
                        "duration_seconds": 0.25,
                        "status": "failed",
                    },
                    {
                        "name": "review_pass",
                        "duration_seconds": 1.5,
                        "status": "blocked",
                        "round": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_timing_report(tmp_path)

    assert "doctor\t0.25s\tfailed" in report
    assert "review_pass\t1.50s\tblocked\t1" in report
