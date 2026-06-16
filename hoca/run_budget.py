from __future__ import annotations

import argparse
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from hoca.contracts import HocaTaskSpec
from hoca.run_state import write_json_atomic


@dataclass(frozen=True)
class RunBudget:
    max_total_rounds: int
    openhands_timeout: int
    openhands_stall: int
    hermes_timeout: int
    risk_level: str
    expected_area_count: int
    repair_round: int
    env_overrides: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _env_int(name: str, default: int, env: dict[str, str]) -> int:
    try:
        return int(env.get(name, default))
    except ValueError:
        return default


def _clamp(value: int, *, floor: int, ceiling: int) -> int:
    return max(floor, min(ceiling, value))


def derive_run_budget(
    spec: HocaTaskSpec,
    *,
    repair_round: int = 1,
    env: dict[str, str] | None = None,
) -> RunBudget:
    source = env or os.environ
    area_count = len([area for area in spec.expected_areas if area.strip()])
    risk = spec.risk_level
    if risk == "low" and area_count <= 1:
        max_rounds, timeout, stall, hermes = 1, 300, 120, 900
    elif risk == "high" or area_count >= 4:
        max_rounds, timeout, stall, hermes = 3, 900, 420, 1800
    else:
        max_rounds, timeout, stall, hermes = 2, 600, 300, 1200

    extra = max(0, repair_round - 1)
    timeout += extra * 120
    stall += extra * 60
    hermes += extra * 180

    floors = {
        "max_total_rounds": _env_int("HOCA_BUDGET_MIN_TOTAL_ROUNDS", 1, source),
        "openhands_timeout": _env_int("HOCA_BUDGET_MIN_OPENHANDS_TIMEOUT", 240, source),
        "openhands_stall": _env_int("HOCA_BUDGET_MIN_OPENHANDS_STALL", 90, source),
        "hermes_timeout": _env_int("HOCA_BUDGET_MIN_HERMES_TIMEOUT", 600, source),
    }
    ceilings = {
        "max_total_rounds": _env_int("HOCA_BUDGET_MAX_TOTAL_ROUNDS", 3, source),
        "openhands_timeout": _env_int("HOCA_BUDGET_MAX_OPENHANDS_TIMEOUT", 1200, source),
        "openhands_stall": _env_int("HOCA_BUDGET_MAX_OPENHANDS_STALL", 600, source),
        "hermes_timeout": _env_int("HOCA_BUDGET_MAX_HERMES_TIMEOUT", 2400, source),
    }
    values = {
        "max_total_rounds": _clamp(
            max_rounds, floor=floors["max_total_rounds"], ceiling=ceilings["max_total_rounds"]
        ),
        "openhands_timeout": _clamp(
            timeout, floor=floors["openhands_timeout"], ceiling=ceilings["openhands_timeout"]
        ),
        "openhands_stall": _clamp(
            stall, floor=floors["openhands_stall"], ceiling=ceilings["openhands_stall"]
        ),
        "hermes_timeout": _clamp(
            hermes, floor=floors["hermes_timeout"], ceiling=ceilings["hermes_timeout"]
        ),
    }
    override_map = {
        "HOCA_MAX_TOTAL_ROUNDS": "max_total_rounds",
        "HOCA_OPENHANDS_TIMEOUT": "openhands_timeout",
        "HOCA_OPENHANDS_STALL": "openhands_stall",
        "HOCA_HERMES_TIMEOUT": "hermes_timeout",
    }
    overrides: dict[str, str] = {}
    for env_name, field_name in override_map.items():
        if source.get(env_name):
            overrides[env_name] = str(source[env_name])
            values[field_name] = _env_int(env_name, int(values[field_name]), source)

    return RunBudget(
        max_total_rounds=int(values["max_total_rounds"]),
        openhands_timeout=int(values["openhands_timeout"]),
        openhands_stall=int(values["openhands_stall"]),
        hermes_timeout=int(values["hermes_timeout"]),
        risk_level=risk,
        expected_area_count=area_count,
        repair_round=repair_round,
        env_overrides=overrides,
    )


def _shell_quote(value: str) -> str:
    if all(ch.isalnum() or ch in "/._-:" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _export_shell(budget: RunBudget) -> str:
    return "\n".join(
        (
            f"MAX_TOTAL_ROUNDS={budget.max_total_rounds}",
            f"export HOCA_OPENHANDS_TIMEOUT={_shell_quote(str(budget.openhands_timeout))}",
            f"export HOCA_OPENHANDS_STALL={_shell_quote(str(budget.openhands_stall))}",
            f"export HOCA_HERMES_TIMEOUT={_shell_quote(str(budget.hermes_timeout))}",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive HOCA per-run budgets.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export-shell")
    export_parser.add_argument("task_spec_path", type=Path)
    export_parser.add_argument("run_dir", type=Path)
    export_parser.add_argument("--round", type=int, default=1)
    args = parser.parse_args(argv)

    spec = HocaTaskSpec.from_json(args.task_spec_path.read_text(encoding="utf-8"))
    budget = derive_run_budget(spec, repair_round=args.round)
    output_path = args.run_dir / f"run-budget-round-{args.round}.json"
    write_json_atomic(output_path, budget.to_dict())
    print(_export_shell(budget))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
