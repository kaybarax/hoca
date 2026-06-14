from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from hoca.contracts import HocaSandboxPolicy
from hoca.sandbox_network import docker_run_network_args, package_install_allowed

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_WRAPPER = REPO_ROOT / "scripts" / "run-openhands-sandboxed.sh"
SANDBOX_DOCKER_ENV = REPO_ROOT / "scripts" / "sandbox-docker-env.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def write_fake_sandbox_tools(fake_bin: Path, state_dir: Path) -> None:
    fake_bin.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
log="{state_dir}/docker.log"
state="{state_dir}/container-running"
printf '%s\\n' "$*" >> "$log"
case "${{1:-}}" in
  image)
    exit 0
    ;;
  inspect)
    if [[ "${{2:-}}" == "--format" ]]; then
      if [[ -f "$state" ]]; then echo true; exit 0; fi
      echo false
      exit 1
    fi
    [[ -f "$state" ]]
    exit $?
    ;;
  run)
    touch "$state"
    echo container-id
    exit 0
    ;;
  rm)
    rm -f "$state"
    exit 0
    ;;
  exec)
    cd "${{FAKE_SANDBOX_PROJECT:?}}"
    bash "${{FAKE_SANDBOX_RUN_DIR:?}}/sandbox-setup.sh"
    printf '%s\\n' '{{"kind":"Status","message":"fake openhands completed"}}'
    exit 0
    ;;
esac
exit 0
""",
    )
    write_executable(
        fake_bin / "pnpm",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{state_dir}/pnpm.log"
if [[ "${{1:-}}" == "--version" ]]; then echo "9.0.0"; exit 0; fi
if [[ "${{1:-}}" == "config" ]]; then exit 0; fi
if [[ "${{1:-}}" == "install" ]]; then mkdir -p node_modules; exit 0; fi
exit 0
""",
    )


def run_sandbox_wrapper(
    project: Path,
    run_dir: Path,
    fake_bin: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "HOCA_PYTHON": sys.executable,
            "HOCA_NETWORK_MODE": "package-install",
            "FAKE_SANDBOX_PROJECT": str(project),
            "FAKE_SANDBOX_RUN_DIR": str(run_dir),
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            str(SANDBOX_WRAPPER),
            str(project),
            "Update README",
            str(run_dir),
            "ollama/test",
            "http://127.0.0.1:11434",
            "ollama",
            "30",
            "10",
            "worker",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


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

    assert 'RUN_DIR_HASH="$(printf \'%s\' "$RUN_DIR" | shasum -a 256' in script
    assert 'CONTAINER_NAME="hoca-${SAFE_AGENT_ROLE}-${SAFE_RUN_ID}-${RUN_DIR_HASH}"' in script
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

    assert 'PNPM_STORE_HOST_DIR="${HOCA_PNPM_STORE_HOST_DIR:-$HOCA_ROOT/.hoca-runtime/pnpm-store}"' in script
    assert 'PNPM_STORE_DIR="${HOCA_PNPM_STORE_DIR:-/hoca-pnpm-store}"' in script
    assert 'mkdir -p "$PNPM_STORE_HOST_DIR"' in script
    assert '-v "${PNPM_STORE_HOST_DIR}:${PNPM_STORE_DIR}"' in script
    assert '-e "PNPM_STORE_DIR=${PNPM_STORE_DIR}"' in script
    assert 'pnpm config set store-dir "${PNPM_STORE_DIR:-/hoca-pnpm-store}"' in script
    assert "/workspace/.pnpm-store" not in script


def test_sandbox_wrapper_skips_pnpm_install_when_cache_matches() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert 'INSTALL_CACHE_MARKER=".hoca-runtime/install-cache/sandbox-pnpm.sha256"' in script
    assert "INSTALL_CACHE_FINGERPRINT=\"$(python3 - <<'PY'" in script
    assert '[ "${HOCA_FORCE_INSTALL:-false}" = "true" ]' in script
    assert "[ ! -d node_modules ]" in script
    assert '!= "$INSTALL_CACHE_FINGERPRINT"' in script
    assert 'printf \'%s\\n\' "$INSTALL_CACHE_FINGERPRINT" > "$INSTALL_CACHE_MARKER"' in script
    assert "Skipping pnpm install; install cache current." in script


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
    assert (
        'printf \'%s\\n\' \\"\\$SETTINGS_FINGERPRINT\\" > \\"\\$SETTINGS_FINGERPRINT_PATH\\"'
        in script
    )


def test_sandbox_wrapper_command_construction_is_static_and_monitored() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert "DOCKER_RUN_ARGS=(" in script
    assert "--workdir /workspace" in script
    assert 'PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd -P)"' in script
    assert 'RUN_DIR="$(cd "$RUN_DIR" && pwd -P)"' in script
    assert (
        'GIT_DIR="$(git -C "$PROJECT_PATH" rev-parse --path-format=absolute --absolute-git-dir'
        in script
    )
    assert (
        'GIT_COMMON_DIR="$(git -C "$PROJECT_PATH" rev-parse --path-format=absolute --git-common-dir'
        in script
    )
    assert 'GIT_DIR_MOUNTS+=("-v" "${GIT_DIR}:${GIT_DIR}")' in script
    assert 'GIT_DIR_MOUNTS+=("-v" "${GIT_COMMON_DIR}:${GIT_COMMON_DIR}")' in script
    assert 'SANDBOX_TASK="${TASK//$PROJECT_PATH/\\/workspace}"' in script
    assert 'SANDBOX_TASK="${SANDBOX_TASK//$RUN_DIR/\\/hoca-run}"' in script
    assert '-v "${PROJECT_PATH}:/workspace"' in script
    assert '-v "${RUN_DIR}:/hoca-run"' in script
    assert '-v "${RUN_DIR}:/hoca-runs"' in script
    assert '-v "${RUN_DIR}:${RUN_DIR}"' in script
    assert 'printf \'%s\' "$SANDBOX_TASK" > "$TASK_FILE"' in script
    assert "TASK_CONTENT=\\$(cat /hoca-run/task-input.txt)" in script
    assert "OPENHANDS_PERSISTENCE_DIR=/hoca-run/openhands-persistence" in script
    assert "OPENHANDS_PYTHON=/opt/openhands-tools/openhands/bin/python" in script
    assert (
        "git config --global --add safe.directory "
        "/home/hoca-sandbox/.openhands/cache/skills/public-skills" in script
    )
    assert "network_mode=offline cannot reach a host-local LLM endpoint" in script
    assert "reasoning_effort=None" in script
    assert "enable_encrypted_reasoning=False" in script
    assert "extended_thinking_budget=None" in script
    assert "load_user_skills=False" in script
    assert "load_public_skills=False" in script
    assert "marketplace_path=None" in script
    assert "cat > /hoca-run/sitecustomize.py <<'PY'" in script
    assert "'skills': []" in script
    assert "AgentStore._build_agent_context = _hoca_build_agent_context" in script
    assert "PYTHONPATH=/hoca-run:\\${PYTHONPATH:-} openhands" in script
    assert 'openhands --headless --task \\"\\$TASK_CONTENT\\" --override-with-envs --json' in script
    assert '"kind": "ConversationErrorEvent"' in script
    assert "monitor_process_stream(" in script
    assert "on_cancel=stop_container" in script
    assert "['docker', 'stop', container_name]" in script
    assert "actor_role=actor_role" in script


def test_sandbox_wrapper_mounts_worktree_git_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    state_dir = tmp_path / "state"
    repo.mkdir()
    run_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "hoca@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "HOCA Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)
    subprocess.run(["git", "worktree", "add", "-b", "task", str(worktree)], cwd=repo, check=True)
    write_fake_sandbox_tools(fake_bin, state_dir)

    result = run_sandbox_wrapper(worktree, run_dir, fake_bin)

    git_dir = subprocess.check_output(
        ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--absolute-git-dir"],
        text=True,
    ).strip()
    git_common_dir = subprocess.check_output(
        ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
    ).strip()
    docker_log = (state_dir / "docker.log").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert f"-v {git_dir}:{git_dir}" in docker_log
    assert f"-v {git_common_dir}:{git_common_dir}" in docker_log


def test_sandbox_wrapper_syncs_yarn_lockfiles_without_npm() -> None:
    script = SANDBOX_WRAPPER.read_text(encoding="utf-8")

    assert "elif [ -f yarn.lock ]" in script
    assert "yarn install --frozen-lockfile" in script
    assert "echo \"  pnpm: $(command -v pnpm 2>/dev/null || echo 'not available')\"" in script
    assert "echo \"  yarn: $(command -v yarn 2>/dev/null || echo 'not available')\"" in script
    assert re.search(r"(^|\s)npm\s+install\b", script) is None


def test_sandbox_wrapper_reuses_container_and_install_cache_across_two_invocations(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    state_dir = tmp_path / "state"
    project.mkdir()
    run_dir.mkdir()
    (project / "package.json").write_text('{"scripts":{"test":"vitest"}}\n', encoding="utf-8")
    (project / "pnpm-lock.yaml").write_text("lock-v1\n", encoding="utf-8")
    write_fake_sandbox_tools(fake_bin, state_dir)

    first = run_sandbox_wrapper(project, run_dir, fake_bin)
    second = run_sandbox_wrapper(project, run_dir, fake_bin)

    docker_log = (state_dir / "docker.log").read_text(encoding="utf-8")
    pnpm_log = (state_dir / "pnpm.log").read_text(encoding="utf-8")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert docker_log.count("run -d") == 1
    assert docker_log.count("exec ") == 2
    assert pnpm_log.count("install --frozen-lockfile") == 1
    assert "Skipping pnpm install; install cache current." in second.stdout
    assert (run_dir / "sandbox-container-name.txt").is_file()
    assert (project / ".hoca-runtime" / "install-cache" / "sandbox-pnpm.sha256").is_file()


def test_sandbox_wrapper_reinstalls_when_lockfile_changes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    run_dir = tmp_path / "run"
    fake_bin = tmp_path / "bin"
    state_dir = tmp_path / "state"
    project.mkdir()
    run_dir.mkdir()
    (project / "package.json").write_text('{"scripts":{"test":"vitest"}}\n', encoding="utf-8")
    (project / "pnpm-lock.yaml").write_text("lock-v1\n", encoding="utf-8")
    write_fake_sandbox_tools(fake_bin, state_dir)

    first = run_sandbox_wrapper(project, run_dir, fake_bin)
    (project / "pnpm-lock.yaml").write_text("lock-v2\n", encoding="utf-8")
    second = run_sandbox_wrapper(project, run_dir, fake_bin)

    docker_log = (state_dir / "docker.log").read_text(encoding="utf-8")
    pnpm_log = (state_dir / "pnpm.log").read_text(encoding="utf-8")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert docker_log.count("run -d") == 1
    assert pnpm_log.count("install --frozen-lockfile") == 2


def test_sandbox_network_helpers_respect_hoca_python() -> None:
    script = SANDBOX_DOCKER_ENV.read_text(encoding="utf-8")

    assert '"${HOCA_PYTHON:-python3}" -m hoca.sandbox_network resolve' in script
    assert '"${HOCA_PYTHON:-python3}" -m hoca.sandbox_network docker-args' in script
    assert '"${HOCA_PYTHON:-python3}" -m hoca.sandbox_network record' in script
