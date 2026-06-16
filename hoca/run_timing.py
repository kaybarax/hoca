"""Machine-readable run timing artifacts for HOCA runs."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hoca.run_state import read_optional_json, write_json_atomic

SCHEMA_VERSION = 1
TIMINGS_FILENAME = "timings.json"


def _now_epoch() -> float:
    return time.time()


def _iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _timings_path(run_dir: Path) -> Path:
    return run_dir / TIMINGS_FILENAME


def _empty_payload(run_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "phases": [],
        "events": [],
        "counters": {
            "agent_loops": 0,
            "container_starts": 0,
            "dependency_installs": 0,
        },
    }


def load_timings(run_dir: Path) -> dict[str, Any]:
    payload = read_optional_json(_timings_path(run_dir))
    if not isinstance(payload, dict):
        return _empty_payload(run_dir)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("run_id", run_dir.name)
    payload.setdefault("phases", [])
    payload.setdefault("events", [])
    counters = payload.setdefault("counters", {})
    counters.setdefault("agent_loops", 0)
    counters.setdefault("container_starts", 0)
    counters.setdefault("dependency_installs", 0)
    return payload


def save_timings(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = _timings_path(run_dir)
    write_json_atomic(path, payload)
    return path


def record_phase(
    run_dir: Path,
    *,
    name: str,
    started_at_epoch: float,
    ended_at_epoch: float | None = None,
    round_number: int | None = None,
    role: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    status: str = "completed",
    metadata: dict[str, Any] | None = None,
) -> Path:
    ended_at_epoch = ended_at_epoch if ended_at_epoch is not None else _now_epoch()
    payload = load_timings(run_dir)
    phase: dict[str, Any] = {
        "name": name,
        "started_at": _iso_from_epoch(started_at_epoch),
        "ended_at": _iso_from_epoch(ended_at_epoch),
        "duration_seconds": round(max(0.0, ended_at_epoch - started_at_epoch), 6),
        "status": status,
    }
    if round_number is not None:
        phase["round"] = round_number
    if role:
        phase["role"] = role
    if mode:
        phase["mode"] = mode
    if model:
        phase["model"] = model
    if metadata:
        phase["metadata"] = metadata
    payload["phases"].append(phase)
    return save_timings(run_dir, payload)


def record_event(
    run_dir: Path,
    *,
    event_type: str,
    name: str,
    round_number: int | None = None,
    role: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    payload = load_timings(run_dir)
    event: dict[str, Any] = {
        "type": event_type,
        "name": name,
        "timestamp": _iso_from_epoch(_now_epoch()),
    }
    if round_number is not None:
        event["round"] = round_number
    if role:
        event["role"] = role
    if metadata:
        event["metadata"] = metadata
    payload["events"].append(event)

    counters = payload["counters"]
    if event_type == "agent_loop":
        counters["agent_loops"] = int(counters.get("agent_loops", 0)) + 1
    elif event_type == "container_start":
        counters["container_starts"] = int(counters.get("container_starts", 0)) + 1
    elif event_type == "dependency_install":
        counters["dependency_installs"] = int(counters.get("dependency_installs", 0)) + 1
    return save_timings(run_dir, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record HOCA run timing artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    phase_parser = subparsers.add_parser("phase", help="Record a completed phase.")
    phase_parser.add_argument("run_dir")
    phase_parser.add_argument("--name", required=True)
    phase_parser.add_argument("--started-at-epoch", type=float, required=True)
    phase_parser.add_argument("--ended-at-epoch", type=float)
    phase_parser.add_argument("--round", type=int)
    phase_parser.add_argument("--role")
    phase_parser.add_argument("--mode")
    phase_parser.add_argument("--model")
    phase_parser.add_argument("--status", default="completed")
    phase_parser.add_argument("--metadata-json", default="")

    event_parser = subparsers.add_parser("event", help="Record a countable timing event.")
    event_parser.add_argument("run_dir")
    event_parser.add_argument("--type", required=True)
    event_parser.add_argument("--name", required=True)
    event_parser.add_argument("--round", type=int)
    event_parser.add_argument("--role")
    event_parser.add_argument("--metadata-json", default="")

    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).resolve()

    try:
        metadata = json.loads(args.metadata_json) if args.metadata_json else None
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata-json must decode to an object")
        if args.command == "phase":
            path = record_phase(
                run_dir,
                name=args.name,
                started_at_epoch=args.started_at_epoch,
                ended_at_epoch=args.ended_at_epoch,
                round_number=args.round,
                role=args.role,
                mode=args.mode,
                model=args.model,
                status=args.status,
                metadata=metadata,
            )
        else:
            path = record_event(
                run_dir,
                event_type=args.type,
                name=args.name,
                round_number=args.round,
                role=args.role,
                metadata=metadata,
            )
        print(path)
    except Exception as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
