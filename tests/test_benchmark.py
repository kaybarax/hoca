from __future__ import annotations

from hoca.benchmark import (
    render_benchmark_comparison,
    render_benchmark_table,
    summarize_benchmark_runs,
)


def test_summarize_benchmark_runs_aggregates_wall_time_and_phases() -> None:
    summary = summarize_benchmark_runs(
        [
            {
                "wall_time_seconds": 10,
                "phase_totals": {"doctor": 1, "worker_attempt": 6},
            },
            {
                "wall_time_seconds": 14,
                "phase_totals": {"doctor": 3, "worker_attempt": 7},
            },
        ]
    )

    assert summary["wall_time"] == {"median": 12.0, "min": 10.0, "max": 14.0}
    assert summary["phases"]["doctor"] == {"median": 2.0, "min": 1.0, "max": 3.0}
    assert summary["phases"]["worker_attempt"] == {"median": 6.5, "min": 6.0, "max": 7.0}


def test_render_benchmark_table_is_comparable_and_sanitized() -> None:
    table = render_benchmark_table(
        {
            "summary": {
                "wall_time": {"median": 12.0, "min": 10.0, "max": 14.0},
                "phases": {"doctor": {"median": 2.0, "min": 1.0, "max": 3.0}},
            }
        }
    )

    assert "| Metric | Median (s) | Min (s) | Max (s) |" in table
    assert "| wall_time | 12.00 | 10.00 | 14.00 |" in table
    assert "private-repo" not in table


def test_render_benchmark_comparison_reports_delta() -> None:
    baseline = {
        "benchmark_id": "PB-DOC-C",
        "summary": {"wall_time": {"median": 100.0}},
    }
    candidate = {
        "benchmark_id": "PB-DOC-C",
        "summary": {"wall_time": {"median": 75.0}},
    }

    table = render_benchmark_comparison(baseline, candidate)

    assert "| PB-DOC-C | 100.00 | 75.00 | -25.00 | -25.00% |" in table
