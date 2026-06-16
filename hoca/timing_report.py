"""Render HOCA run timing artifacts."""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Any

from hoca.run_state import read_optional_json, runtime_archive_root
from hoca.run_timing import load_timings


def _fmt_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{seconds:.2f}s"


def _phase_sort_key(phase: dict[str, Any]) -> str:
    return str(phase.get("started_at") or "")


def _rounds_used(phases: list[dict[str, Any]]) -> list[int]:
    return sorted(
        {
            int(phase["round"])
            for phase in phases
            if isinstance(phase.get("round"), int) and int(phase["round"]) > 0
        }
    )


def summarize_timing_run(run_dir: Path) -> dict[str, Any]:
    timings = load_timings(run_dir)
    phases = [phase for phase in timings.get("phases", []) if isinstance(phase, dict)]
    phases.sort(key=_phase_sort_key)
    counters = timings.get("counters") if isinstance(timings.get("counters"), dict) else {}
    status = read_optional_json(run_dir / "status.json") or {}
    final_state = read_optional_json(run_dir / "final-state.json") or {}
    return {
        "run_dir": run_dir,
        "run_id": timings.get("run_id") or run_dir.name,
        "phases": phases,
        "wall_time_seconds": sum(float(phase.get("duration_seconds") or 0.0) for phase in phases),
        "rounds": len(_rounds_used(phases)),
        "agent_loops": int(counters.get("agent_loops", 0) or 0),
        "container_starts": int(counters.get("container_starts", 0) or 0),
        "dependency_installs": int(counters.get("dependency_installs", 0) or 0),
        "status": str(status.get("status") or ""),
        "reason": str(status.get("reason") or ""),
        "final_status": str(final_state.get("status") or ""),
    }


def build_timing_report(run_dir: Path) -> str:
    summary = summarize_timing_run(run_dir)
    phases = summary["phases"]

    lines = [
        "HOCA Timing Report",
        f"Run ID: {summary['run_id']}",
        f"Wall time: {_fmt_seconds(summary['wall_time_seconds'])}",
        f"Rounds used: {summary['rounds']}",
        f"Agent loops: {summary['agent_loops']}",
        f"Container starts: {summary['container_starts']}",
        f"Dependency installs: {summary['dependency_installs']}",
        "",
        "PHASE\tDURATION\tSTATUS\tROUND\tROLE\tMODE\tMODEL",
    ]
    for phase in phases:
        lines.append(
            "\t".join(
                (
                    str(phase.get("name") or "-"),
                    _fmt_seconds(phase.get("duration_seconds")),
                    str(phase.get("status") or "-"),
                    str(phase.get("round") or "-"),
                    str(phase.get("role") or "-"),
                    str(phase.get("mode") or "-"),
                    str(phase.get("model") or "-"),
                )
            )
        )
    return "\n".join(lines) + "\n"


def _archive_run_dirs(project_path: Path) -> list[Path]:
    archive_root = runtime_archive_root() / project_path.resolve().name
    if not archive_root.is_dir():
        return []
    return sorted(
        (path for path in archive_root.iterdir() if path.is_dir()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )


def _summarize_values(values: list[float]) -> tuple[float, float, float]:
    return (
        round(statistics.median(values), 6),
        round(min(values), 6),
        round(max(values), 6),
    )


def build_timing_trends_report(project_path: Path) -> str:
    run_dirs = _archive_run_dirs(project_path)
    summaries = [summarize_timing_run(run_dir) for run_dir in run_dirs]
    archive_root = runtime_archive_root() / project_path.resolve().name

    lines = [
        "HOCA Timing Trend Report",
        f"Project: {project_path.name}",
        f"Archive root: {archive_root}",
        f"Archived runs: {len(summaries)}",
    ]

    if not summaries:
        lines.append("- No archived timing runs found.")
        return "\n".join(lines) + "\n"

    wall_times = [float(summary["wall_time_seconds"]) for summary in summaries]
    agent_loops = [float(summary["agent_loops"]) for summary in summaries]
    container_starts = [float(summary["container_starts"]) for summary in summaries]
    dependency_installs = [float(summary["dependency_installs"]) for summary in summaries]

    wall_median, wall_min, wall_max = _summarize_values(wall_times)
    loops_median, loops_min, loops_max = _summarize_values(agent_loops)
    containers_median, containers_min, containers_max = _summarize_values(container_starts)
    installs_median, installs_min, installs_max = _summarize_values(dependency_installs)

    lines.extend(
        [
            f"Wall time median: {_fmt_seconds(wall_median)} (min {_fmt_seconds(wall_min)}, max {_fmt_seconds(wall_max)})",
            f"Agent loops median: {loops_median:.0f} (min {loops_min:.0f}, max {loops_max:.0f})",
            f"Container starts median: {containers_median:.0f} (min {containers_min:.0f}, max {containers_max:.0f})",
            f"Dependency installs median: {installs_median:.0f} (min {installs_min:.0f}, max {installs_max:.0f})",
            "",
            "RUN ID\tWALL TIME\tDELTA\tROUNDS\tAGENT LOOPS\tCONTAINERS\tINSTALLS\tSTATUS\tFINAL",
        ]
    )

    previous_wall_time: float | None = None
    for summary in summaries:
        wall_time = float(summary["wall_time_seconds"])
        delta = "-" if previous_wall_time is None else f"{wall_time - previous_wall_time:+.2f}s"
        lines.append(
            "\t".join(
                (
                    str(summary["run_id"]),
                    _fmt_seconds(wall_time),
                    delta,
                    str(summary["rounds"]),
                    str(summary["agent_loops"]),
                    str(summary["container_starts"]),
                    str(summary["dependency_installs"]),
                    str(summary["status"] or "-"),
                    str(summary["final_status"] or "-"),
                )
            )
        )
        previous_wall_time = wall_time

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a HOCA timing report.")
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)
    print(build_timing_report(Path(args.run_dir)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
