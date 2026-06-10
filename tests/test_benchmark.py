from __future__ import annotations

from pathlib import Path

from hoca.benchmark import (
    _artifact_audit,
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


def test_artifact_audit_reports_gate_artifacts(tmp_path) -> None:
    run_dir = tmp_path / "run"
    attempts = run_dir / "attempts"
    attempts.mkdir(parents=True)
    (attempts / "worker-attempt-1.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "status.json").write_text('{"status":"reviewing","reason":"ok"}\n', encoding="utf-8")
    (run_dir / "tests-summary.md").write_text("passed\n", encoding="utf-8")
    (run_dir / "review-report.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "final-state.json").write_text(
        '{"status":"pr_opened"}\n',
        encoding="utf-8",
    )

    audit = _artifact_audit(run_dir)

    assert audit == {
        "available": True,
        "status": "reviewing",
        "reason": "ok",
        "has_worker_attempt": True,
        "has_tests_summary": True,
        "has_review": True,
        "has_final_state": True,
        "final_status": "pr_opened",
    }


def test_artifact_audit_handles_missing_run_dir() -> None:
    assert _artifact_audit(None) == {"available": False}


def test_benchmark_runs_skip_pr_creation_for_validation_clones() -> None:
    content = (Path(__file__).resolve().parents[1] / "hoca" / "benchmark.py").read_text(
        encoding="utf-8"
    )

    assert '"HOCA_KEEP_RUNTIME": "false"' in content
    assert '"HOCA_SKIP_PR_CREATION": "true"' in content
