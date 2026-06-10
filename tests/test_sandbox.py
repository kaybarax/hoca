from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hoca.contracts import HocaSandboxPolicy
from hoca.sandbox_network import docker_run_network_args, package_install_allowed

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_WRAPPER = REPO_ROOT / "scripts" / "run-openhands-sandboxed.sh"
SANDBOX_DOCKER_ENV = REPO_ROOT / "scripts" / "sandbox-docker-env.sh"


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
    assert '--user "$SANDBOX_USER"' in script
    assert '--memory="${HOCA_SANDBOX_MEMORY:-8g}"' in script
    assert '--pids-limit="${HOCA_SANDBOX_PIDS:-512}"' in script


def test_sandbox_wrapper_uses_run_scoped_container_with_exec_env() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert 'CONTAINER_NAME="hoca-worker-${RUN_ID}"' in script
    assert 'CONTAINER_NAME_FILE="$RUN_DIR/sandbox-container-name.txt"' in script
    assert "docker run -d \\" in script
    assert "sleep infinity >/dev/null" in script
    assert "docker exec \\" in script
    assert '-e "LLM_MODEL=${MODEL}"' in script
    assert '-e "LLM_BASE_URL=${CONTAINER_BASE_URL}"' in script
    assert '-e "LLM_API_KEY=${API_KEY}"' in script
    assert '-e "HOCA_AGENT_ROLE=${AGENT_ROLE}"' in script


def test_sandbox_wrapper_checks_image_once_per_run() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert 'IMAGE_READY_FILE="$RUN_DIR/sandbox-image-ready.txt"' in script
    assert '[ ! -f "$IMAGE_READY_FILE" ] && ! docker image inspect "$SANDBOX_IMAGE"' in script
    assert 'printf \'%s\\n\' "$SANDBOX_IMAGE" > "$IMAGE_READY_FILE"' in script


def test_sandbox_wrapper_mounts_shared_pnpm_store_volume() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert 'PNPM_STORE_VOLUME="${HOCA_PNPM_STORE_VOLUME:-hoca-pnpm-store}"' in script
    assert 'PNPM_STORE_DIR="${HOCA_PNPM_STORE_DIR:-/hoca-pnpm-store}"' in script
    assert '-v "${PNPM_STORE_VOLUME}:${PNPM_STORE_DIR}"' in script
    assert '-e "PNPM_STORE_DIR=${PNPM_STORE_DIR}"' in script
    assert 'pnpm config set store-dir "${PNPM_STORE_DIR:-/hoca-pnpm-store}"' in script
    assert "/workspace/.pnpm-store" not in script


def test_sandbox_wrapper_caches_openhands_settings_by_model_inputs() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert 'SETTINGS_PATH=\\"\\$OPENHANDS_PERSISTENCE_DIR/agent_settings.json\\"' in script
    assert (
        'SETTINGS_FINGERPRINT_PATH=\\"\\$OPENHANDS_PERSISTENCE_DIR/agent_settings.fingerprint\\"'
        in script
    )
    assert "hashlib.sha256(payload).hexdigest()" in script
    assert (
        '[ ! -f \\"\\$SETTINGS_PATH\\" ] || [ \\"\\$(cat \\"\\$SETTINGS_FINGERPRINT_PATH\\" 2>/dev/null || true)\\" != \\"\\$SETTINGS_FINGERPRINT\\" ]'
        in script
    )
    assert 'printf \'%s\\n\' \\"\\$SETTINGS_FINGERPRINT\\" > \\"\\$SETTINGS_FINGERPRINT_PATH\\"' in script


def test_sandbox_wrapper_command_construction_is_static_and_monitored() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert "DOCKER_RUN_ARGS=(" in script
    assert "--workdir /workspace" in script
    assert 'PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd -P)"' in script
    assert 'RUN_DIR="$(cd "$RUN_DIR" && pwd -P)"' in script
    assert 'SANDBOX_TASK="${TASK//$PROJECT_PATH/\\/workspace}"' in script
    assert 'SANDBOX_TASK="${SANDBOX_TASK//$RUN_DIR/\\/hoca-run}"' in script
    assert '-v "${PROJECT_PATH}:/workspace"' in script
    assert '-v "${RUN_DIR}:/hoca-run"' in script
    assert '-v "${RUN_DIR}:${RUN_DIR}"' in script
    assert 'printf \'%s\' "$SANDBOX_TASK" > "$TASK_FILE"' in script
    assert "TASK_CONTENT=\\$(cat /hoca-run/task-input.txt)" in script
    assert "OPENHANDS_PERSISTENCE_DIR=/hoca-run/openhands-persistence" in script
    assert "OPENHANDS_PYTHON=/opt/openhands-tools/openhands/bin/python" in script
    assert "reasoning_effort=None" in script
    assert "enable_encrypted_reasoning=False" in script
    assert "extended_thinking_budget=None" in script
    assert 'openhands --headless --task \\"\\$TASK_CONTENT\\" --override-with-envs --json' in script
    assert '"kind": "ConversationErrorEvent"' in script
    assert "monitor_process_stream(" in script
    assert "actor_role=actor_role" in script


def test_sandbox_wrapper_syncs_yarn_lockfiles_without_npm() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert "elif [ -f yarn.lock ]" in script
    assert "yarn install --frozen-lockfile" in script
    assert re.search(r"(^|\s)npm\s+install\b", script) is None


def test_sandbox_network_helpers_respect_hoca_python() -> None:
    script = SANDBOX_DOCKER_ENV.read_text(encoding="utf-8")

    assert '"${HOCA_PYTHON:-python3}" -m hoca.sandbox_network resolve' in script
    assert '"${HOCA_PYTHON:-python3}" -m hoca.sandbox_network docker-args' in script
    assert '"${HOCA_PYTHON:-python3}" -m hoca.sandbox_network record' in script
