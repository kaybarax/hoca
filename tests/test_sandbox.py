from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoca.contracts import HocaSandboxPolicy
from hoca.sandbox_network import docker_run_network_args, package_install_allowed

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_WRAPPER = REPO_ROOT / "scripts" / "run-openhands-sandboxed.sh"


def test_sandbox_policy_defaults_to_enabled_offline() -> None:
    policy = HocaSandboxPolicy()

    assert policy.enabled is True
    assert policy.network_mode == "offline"
    assert docker_run_network_args(policy.network_mode) == ["--network", "none"]
    assert package_install_allowed(policy.network_mode) is False


def test_sandbox_policy_round_trips_without_credentials() -> None:
    policy = HocaSandboxPolicy(enabled=True, network_mode="package-install")

    payload = json.loads(policy.to_json())
    assert payload == {
        "enabled": True,
        "network_mode": "package-install",
        "schema_version": 1,
    }
    assert "api_key" not in policy.to_json().lower()
    assert HocaSandboxPolicy.from_dict(payload) == policy


def test_sandbox_policy_requires_known_network_mode() -> None:
    with pytest.raises(ValueError, match="network_mode"):
        HocaSandboxPolicy.from_dict({"enabled": True, "network_mode": "wide-open"})


def test_sandbox_wrapper_does_not_forward_manager_credentials() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert "GITHUB_TOKEN" not in script
    assert "SSH_AUTH_SOCK" not in script
    assert "AWS_ACCESS_KEY_ID" not in script
    assert "docker.sock" not in script.lower()
    assert '-e "LLM_API_KEY=${API_KEY}"' in script


def test_sandbox_wrapper_keeps_container_hardened() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert "--security-opt=no-new-privileges" in script
    assert "--cap-drop=ALL" in script
    assert "--user \"$SANDBOX_USER\"" in script
    assert "--memory=\"${HOCA_SANDBOX_MEMORY:-8g}\"" in script
    assert "--pids-limit=\"${HOCA_SANDBOX_PIDS:-512}\"" in script
