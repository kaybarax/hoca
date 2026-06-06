from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from hoca.fleet_registry import FleetRegistry


def _process_rows() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-Ao", "pid,ppid,%cpu,rss,command"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        try:
            rows.append(
                {
                    "pid": int(parts[0]),
                    "ppid": int(parts[1]),
                    "cpu_pct": float(parts[2]),
                    "rss_kb": int(parts[3]),
                    "command": parts[4],
                }
            )
        except ValueError:
            continue
    return rows


def _matches_lane(row: dict[str, Any], lane_id: str, run_dir: str) -> bool:
    command = str(row.get("command") or "")
    return bool(lane_id and lane_id in command) or bool(run_dir and run_dir in command)


def collect_resource_sample(registry: FleetRegistry) -> dict[str, Any]:
    rows = _process_rows()
    lanes = registry.list_lanes()
    per_lane: dict[str, dict[str, Any]] = {}
    matched_pids: set[int] = set()
    for lane in lanes:
        lane_rows = [
            row for row in rows if _matches_lane(row, lane.lane_id, lane.run_dir or "")
        ]
        for row in lane_rows:
            matched_pids.add(int(row["pid"]))
        per_lane[lane.lane_id] = {
            "process_count": len(lane_rows),
            "cpu_pct": round(sum(float(row["cpu_pct"]) for row in lane_rows), 3),
            "rss_mb": round(sum(int(row["rss_kb"]) for row in lane_rows) / 1024, 3),
        }

    matched_rows = [row for row in rows if int(row["pid"]) in matched_pids]
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "aggregate": {
            "process_count": len(matched_rows),
            "cpu_pct": round(sum(float(row["cpu_pct"]) for row in matched_rows), 3),
            "rss_mb": round(sum(int(row["rss_kb"]) for row in matched_rows) / 1024, 3),
        },
        "lanes": per_lane,
    }


def summarize_resource_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "sample_count": 0,
            "peak_cpu_pct": 0.0,
            "average_cpu_pct": 0.0,
            "peak_rss_mb": 0.0,
            "average_rss_mb": 0.0,
            "peak_process_count": 0,
        }
    cpu_values = [float(sample["aggregate"]["cpu_pct"]) for sample in samples]
    rss_values = [float(sample["aggregate"]["rss_mb"]) for sample in samples]
    process_values = [int(sample["aggregate"]["process_count"]) for sample in samples]
    return {
        "sample_count": len(samples),
        "peak_cpu_pct": round(max(cpu_values), 3),
        "average_cpu_pct": round(sum(cpu_values) / len(cpu_values), 3),
        "peak_rss_mb": round(max(rss_values), 3),
        "average_rss_mb": round(sum(rss_values) / len(rss_values), 3),
        "peak_process_count": max(process_values),
    }


def write_resource_monitor_report(
    registry: FleetRegistry,
    *,
    output: Path,
    interval_seconds: float,
    samples: int,
) -> dict[str, Any]:
    collected: list[dict[str, Any]] = []
    started = time.monotonic()
    for index in range(samples):
        if index:
            time.sleep(interval_seconds)
        collected.append(collect_resource_sample(registry))
    duration_seconds = round(time.monotonic() - started, 3)
    report = {
        "schema_version": 1,
        "duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "samples": collected,
        "summary": summarize_resource_samples(collected),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
