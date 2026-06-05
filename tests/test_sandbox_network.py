from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoca.config import HocaConfig
from hoca.contracts import HocaSandboxPolicy
from hoca.run_layout import sandbox_policy_path, task_spec_path
from hoca.sandbox_network import (
    NetworkModeError,
    docker_run_network_args,
    normalize_network_mode,
    package_install_allowed,
    record_effective_sandbox_policy,
    resolve_network_mode,
)


def test_normalize_network_mode_accepts_all_modes() -> None:
    for mode in ("offline", "package-install", "github-only", "full"):
        assert normalize_network_mode(mode) == mode


def test_normalize_network_mode_rejects_unknown() -> None:
    with pytest.raises(NetworkModeError, match="network_mode"):
        normalize_network_mode("wide-open")


def test_docker_args_offline_uses_network_none() -> None:
    assert docker_run_network_args("offline") == ["--network", "none"]


def test_docker_args_bridge_modes_use_default_network() -> None:
    for mode in ("package-install", "github-only", "full"):
        assert docker_run_network_args(mode) == []


def test_package_install_allowed_only_for_bridge_modes() -> None:
    assert package_install_allowed("offline") is False
    assert package_install_allowed("package-install") is True
    assert package_install_allowed("full") is True


def test_resolve_defaults_to_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOCA_NETWORK_MODE", raising=False)
    assert resolve_network_mode(role="worker", run_dir=tmp_path) == "offline"


def test_resolve_uses_task_spec_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOCA_NETWORK_MODE", "offline")
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    spec = {
        "schema_version": 1,
        "sandbox": {"enabled": True, "network_mode": "package-install"},
    }
    task_spec_path(run_dir).write_text(json.dumps(spec), encoding="utf-8")
    assert resolve_network_mode(role="worker", run_dir=run_dir) == "package-install"


def test_reviewer_prefers_offline_even_when_task_spec_is_broader(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    spec = {
        "schema_version": 1,
        "sandbox": {"enabled": True, "network_mode": "package-install"},
    }
    task_spec_path(run_dir).write_text(json.dumps(spec), encoding="utf-8")
    assert resolve_network_mode(role="reviewer", run_dir=run_dir) == "offline"


def test_full_requires_explicit_opt_in_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOCA_NETWORK_MODE", "full")
    assert resolve_network_mode(role="worker", run_dir=tmp_path, env_mode="full") == "full"


def test_task_spec_full_is_explicit_opt_in(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    spec = {
        "schema_version": 1,
        "sandbox": {"enabled": True, "network_mode": "full"},
    }
    task_spec_path(run_dir).write_text(json.dumps(spec), encoding="utf-8")
    assert resolve_network_mode(role="worker", run_dir=run_dir) == "full"


def test_record_effective_sandbox_policy_writes_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    sandbox_policy_path(run_dir).write_text(
        HocaSandboxPolicy(enabled=True, network_mode="offline").to_json(),
        encoding="utf-8",
    )
    path = record_effective_sandbox_policy(run_dir, role="reviewer", effective_mode="offline")
    assert path == sandbox_policy_path(run_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["effective_network_mode"] == "offline"
    assert payload["resolved_for_role"] == "reviewer"
    assert payload["docker_network_args"] == ["--network", "none"]
    assert "limitations" in payload


def test_hoca_config_default_network_mode_offline() -> None:
    cfg = HocaConfig()
    assert cfg.network_mode == "offline"


def test_sandbox_policy_rejects_invalid_network_mode() -> None:
    with pytest.raises(ValueError, match="network_mode"):
        HocaSandboxPolicy.from_dict({"enabled": True, "network_mode": "wide-open"})
