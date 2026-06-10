"""Render HOCA run timing artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hoca.run_timing import load_timings


def _fmt_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{seconds:.2f}s"


def _phase_sort_key(phase: dict[str, Any]) -> str:
    return str(phase.get("started_at") or "")


def build_timing_report(run_dir: Path) -> str:
    timings = load_timings(run_dir)
    phases = [phase for phase in timings.get("phases", []) if isinstance(phase, dict)]
    phases.sort(key=_phase_sort_key)
    counters = timings.get("counters") if isinstance(timings.get("counters"), dict) else {}
    wall_time = sum(float(phase.get("duration_seconds") or 0.0) for phase in phases)
    rounds = sorted(
        {
            int(phase["round"])
            for phase in phases
            if isinstance(phase.get("round"), int) and int(phase["round"]) > 0
        }
    )

    lines = [
        "HOCA Timing Report",
        f"Run ID: {timings.get('run_id') or run_dir.name}",
        f"Wall time: {_fmt_seconds(wall_time)}",
        f"Rounds used: {len(rounds)}",
        f"Agent loops: {int(counters.get('agent_loops', 0) or 0)}",
        f"Container starts: {int(counters.get('container_starts', 0) or 0)}",
        f"Dependency installs: {int(counters.get('dependency_installs', 0) or 0)}",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a HOCA timing report.")
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)
    print(build_timing_report(Path(args.run_dir)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
