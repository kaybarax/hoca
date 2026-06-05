from __future__ import annotations

from pathlib import Path

from hoca.control_paths import CONTROL_ROOT_ENV, control_root, make_fleet_control_paths
import pytest


def test_control_root_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_root = Path("/tmp/hoca-control-root")
    monkeypatch.setenv(CONTROL_ROOT_ENV, str(tmp_root))
    assert control_root() == tmp_root.resolve()


def test_control_root_uses_override() -> None:
    override = Path("/tmp/override-control-root")
    assert control_root(override=override) == override.resolve()


def test_control_root_defaults_to_home() -> None:
    resolved = control_root()
    assert resolved.is_absolute()
    assert str(resolved).startswith(str(Path.home()))


def test_control_paths_are_absolute_and_created(tmp_path: Path) -> None:
    paths = make_fleet_control_paths(override=tmp_path / "fleet-control")
    assert paths.root.is_absolute()
    assert paths.root.exists()
    assert paths.memory_dir.exists()
    assert paths.projects_json == paths.root / "projects.json"
    assert paths.tasks_json == paths.root / "tasks.json"
    assert paths.lanes_json == paths.root / "lanes.json"
    assert paths.agent_adapters_json == paths.root / "agent-adapters.json"
    assert paths.resource_state_json == paths.root / "resource-state.json"
