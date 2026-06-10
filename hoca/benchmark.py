"""HOCA benchmark harness for canonical performance scenarios."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoca.fleet_registry import FleetRegistry
from hoca.fleet_resources import collect_resource_sample, summarize_resource_samples
from hoca.run_timing import load_timings


@dataclass(frozen=True)
class BenchmarkTask:
    benchmark_id: str
    repo_alias: str
    task: str


BENCHMARK_TASKS = {
    "PB-FEATURE-A": BenchmarkTask(
        benchmark_id="PB-FEATURE-A",
        repo_alias="<VALIDATION_REPO_A>",
        task=(
            "In src/App.tsx, add a compact visible label that says `Starter ready`. "
            "Add or update a test if this repository has an existing suitable test "
            "location; otherwise keep the change scoped to the UI file. Acceptance "
            "criteria: `yarn build` passes. Do not modify unrelated files."
        ),
    ),
    "PB-DOC-C": BenchmarkTask(
        benchmark_id="PB-DOC-C",
        repo_alias="<VALIDATION_REPO_C>",
        task=(
            "In README.md, add a short contributor note under an appropriate existing "
            "section that says validation-only changes should be kept small, documented, "
            "and easy to revert. Do not modify code or unrelated files. Acceptance "
            "criteria: documentation remains readable; no automated tests are required "
            "for this doc-only change."
        ),
    ),
}


def _latest_run_dir(repo: Path, archive_root: Path) -> Path | None:
    candidates: list[Path] = []
    live_runs = repo / ".hoca-runtime" / "runs"
    if live_runs.is_dir():
        candidates.extend(path for path in live_runs.iterdir() if path.is_dir())
    archived_runs = archive_root / repo.name
    if archived_runs.is_dir():
        candidates.extend(path for path in archived_runs.iterdir() if path.is_dir())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _phase_totals(timings: dict[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for phase in timings.get("phases", []):
        if not isinstance(phase, dict):
            continue
        name = str(phase.get("name") or "")
        if not name:
            continue
        totals[name] = round(totals.get(name, 0.0) + float(phase.get("duration_seconds") or 0.0), 6)
    return totals


def _artifact_audit(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {"available": False}
    status_path = run_dir / "status.json"
    final_path = run_dir / "final-state.json"
    status: dict[str, Any] = {}
    final_state: dict[str, Any] = {}
    if status_path.is_file():
        try:
            loaded = json.loads(status_path.read_text(encoding="utf-8"))
            status = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            status = {}
    if final_path.is_file():
        try:
            loaded = json.loads(final_path.read_text(encoding="utf-8"))
            final_state = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            final_state = {}
    attempts_dir = run_dir / "attempts"
    return {
        "available": True,
        "status": status.get("status", ""),
        "reason": status.get("reason", ""),
        "has_worker_attempt": bool(list(attempts_dir.glob("worker-attempt-*.json")))
        if attempts_dir.is_dir()
        else False,
        "has_tests_summary": (run_dir / "tests-summary.md").is_file(),
        "has_review": (run_dir / "review-report.json").is_file()
        or (run_dir / "openhands-review.txt").is_file(),
        "has_final_state": final_path.is_file(),
        "final_status": final_state.get("status", ""),
    }


def summarize_benchmark_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    wall_times = [float(run["wall_time_seconds"]) for run in runs]
    phase_names = sorted({name for run in runs for name in run.get("phase_totals", {})})
    phase_summary: dict[str, dict[str, float]] = {}
    for name in phase_names:
        values = [float(run.get("phase_totals", {}).get(name, 0.0)) for run in runs]
        phase_summary[name] = {
            "median": round(statistics.median(values), 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
        }
    return {
        "runs": len(runs),
        "wall_time": {
            "median": round(statistics.median(wall_times), 6) if wall_times else 0.0,
            "min": round(min(wall_times), 6) if wall_times else 0.0,
            "max": round(max(wall_times), 6) if wall_times else 0.0,
        },
        "phases": phase_summary,
    }


def render_benchmark_table(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "| Metric | Median (s) | Min (s) | Max (s) |",
        "| --- | ---: | ---: | ---: |",
        (
            "| wall_time | "
            f"{summary['wall_time']['median']:.2f} | "
            f"{summary['wall_time']['min']:.2f} | "
            f"{summary['wall_time']['max']:.2f} |"
        ),
    ]
    for name, values in summary["phases"].items():
        lines.append(
            f"| {name} | {values['median']:.2f} | {values['min']:.2f} | {values['max']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def render_benchmark_comparison(baseline: dict[str, Any], candidate: dict[str, Any]) -> str:
    baseline_wall = float(baseline["summary"]["wall_time"]["median"])
    candidate_wall = float(candidate["summary"]["wall_time"]["median"])
    delta = round(candidate_wall - baseline_wall, 6)
    percent = round((delta / baseline_wall) * 100, 2) if baseline_wall else 0.0
    lines = [
        "| Benchmark | Baseline Median (s) | Candidate Median (s) | Delta (s) | Delta % |",
        "| --- | ---: | ---: | ---: | ---: |",
        (
            f"| {baseline.get('benchmark_id', 'benchmark')} | {baseline_wall:.2f} | "
            f"{candidate_wall:.2f} | {delta:.2f} | {percent:.2f}% |"
        ),
    ]
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_benchmark(
    *,
    benchmark_id: str,
    repo: Path,
    runs: int,
    output: Path,
    hoca_script: Path,
    resource_samples: int = 0,
) -> dict[str, Any]:
    benchmark = BENCHMARK_TASKS[benchmark_id]
    archive_root = output.parent / "runtime-archives"
    run_records: list[dict[str, Any]] = []
    resource_records: list[dict[str, Any]] = []

    scratch_root = output.parent / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hoca-bench-", dir=scratch_root) as temp_root:
        root = Path(temp_root)
        for index in range(1, runs + 1):
            clone = root / f"{benchmark_id.lower()}-{index}"
            subprocess.run(["git", "clone", "--quiet", str(repo), str(clone)], check=True)
            started = time.monotonic()
            completed = subprocess.run(
                [str(hoca_script), str(clone), benchmark.task, "--timing"],
                check=False,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "HOCA_RUNTIME_ARCHIVE_ROOT": str(archive_root),
                    "HOCA_KEEP_RUNTIME": "false",
                },
            )
            ended = time.monotonic()
            for _ in range(resource_samples):
                resource_records.append(collect_resource_sample(FleetRegistry()))
            run_dir = _latest_run_dir(clone, archive_root)
            timings = load_timings(run_dir) if run_dir is not None else {}
            counters = timings.get("counters") if isinstance(timings.get("counters"), dict) else {}
            run_records.append(
                {
                    "run_number": index,
                    "exit_code": completed.returncode,
                    "wall_time_seconds": round(ended - started, 6),
                    "rounds": len(
                        {
                            phase.get("round")
                            for phase in timings.get("phases", [])
                            if isinstance(phase, dict) and phase.get("round")
                        }
                    ),
                    "agent_loops": int(counters.get("agent_loops", 0) or 0),
                    "container_starts": int(counters.get("container_starts", 0) or 0),
                    "dependency_installs": int(counters.get("dependency_installs", 0) or 0),
                    "phase_totals": _phase_totals(timings),
                    "artifact_audit": _artifact_audit(run_dir),
                }
            )

    result = {
        "schema_version": 1,
        "benchmark_id": benchmark.benchmark_id,
        "repo_alias": benchmark.repo_alias,
        "task": benchmark.task,
        "runs": run_records,
        "summary": summarize_benchmark_runs(run_records),
        "resource_summary": summarize_resource_samples(resource_records),
    }
    _write_json(output, result)
    return result
