from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CONTROL_ROOT_ENV = "HOCA_CONTROL_ROOT"


def control_root(*, override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    env_value = os.environ.get(CONTROL_ROOT_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()
    return (Path.home() / ".hoca" / "control").resolve()


@dataclass(frozen=True)
class FleetControlPaths:
    root: Path

    @property
    def projects_json(self) -> Path:
        return self.root / "projects.json"

    @property
    def tasks_json(self) -> Path:
        return self.root / "tasks.json"

    @property
    def lanes_json(self) -> Path:
        return self.root / "lanes.json"

    @property
    def agent_adapters_json(self) -> Path:
        return self.root / "agent-adapters.json"

    @property
    def resource_state_json(self) -> Path:
        return self.root / "resource-state.json"

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    def ensure_structure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)


def make_fleet_control_paths(*, override: str | Path | None = None) -> FleetControlPaths:
    cfg = FleetControlPaths(control_root(override=override))
    cfg.ensure_structure()
    return cfg
