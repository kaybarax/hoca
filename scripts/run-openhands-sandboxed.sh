#!/usr/bin/env bash
set -euo pipefail

# Runs OpenHands inside a Docker sandbox container.
# The container has bun, node, pnpm, git, and OpenHands pre-installed.
# The project is mounted at /workspace and HOCA's monitor watches stdout.

if [ "$#" -lt 8 ]; then
  echo "Usage: run-openhands-sandboxed.sh <project-path> <task> <run-dir> <model> <base-url> <api-key> <timeout> <stall> [agent-role]"
  exit 1
fi

PROJECT_PATH="$1"
TASK="$2"
RUN_DIR="$3"
MODEL="$4"
BASE_URL="$5"
API_KEY="$6"
TIMEOUT="$7"
STALL="$8"
AGENT_ROLE="${9:-worker}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/sandbox-docker-env.sh
source "$SCRIPT_DIR/sandbox-docker-env.sh"

SANDBOX_IMAGE="${HOCA_SANDBOX_IMAGE:-hoca-sandbox:latest}"
DEPS_STORE_DIR="${HOCA_DEPS_STORE_DIR:-/hoca-deps-store}"
DEPS_STORE_HOST_DIR="${HOCA_DEPS_STORE_HOST_DIR:-$HOCA_ROOT/.hoca-runtime/deps-store}"
RUN_ID="$(basename "$RUN_DIR")"

PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd -P)"
mkdir -p "$RUN_DIR"
RUN_DIR="$(cd "$RUN_DIR" && pwd -P)"
mkdir -p "$DEPS_STORE_HOST_DIR"
GIT_DIR_MOUNTS=()
if GIT_DIR="$(git -C "$PROJECT_PATH" rev-parse --path-format=absolute --absolute-git-dir 2>/dev/null)" && [ -n "$GIT_DIR" ]; then
  GIT_COMMON_DIR="$(git -C "$PROJECT_PATH" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || printf '%s' "$GIT_DIR")"
  GIT_DIR_MOUNTS+=("-v" "${GIT_DIR}:${GIT_DIR}")
  if [ -n "$GIT_COMMON_DIR" ] && [ "$GIT_COMMON_DIR" != "$GIT_DIR" ]; then
    GIT_DIR_MOUNTS+=("-v" "${GIT_COMMON_DIR}:${GIT_COMMON_DIR}")
  fi
fi
RUN_DIR_HASH="$(printf '%s' "$RUN_DIR" | shasum -a 256 | awk '{print substr($1,1,12)}')"
SAFE_RUN_ID="$(printf '%s' "$RUN_ID" | tr -c 'A-Za-z0-9_.-' '-')"
SAFE_AGENT_ROLE="$(printf '%s' "$AGENT_ROLE" | tr -c 'A-Za-z0-9_.-' '-')"
CONTAINER_NAME="hoca-${SAFE_AGENT_ROLE}-${SAFE_RUN_ID}-${RUN_DIR_HASH}"
CONTAINER_NAME_FILE="$RUN_DIR/sandbox-container-name.txt"
IMAGE_READY_FILE="$RUN_DIR/sandbox-image-ready.txt"

# Ensure sandbox image exists once per run.
if [ ! -f "$IMAGE_READY_FILE" ] && ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  echo "Building sandbox image..."
  docker build -t "$SANDBOX_IMAGE" -f "$HOCA_ROOT/docker/Dockerfile.sandbox" "$HOCA_ROOT/docker"
fi
printf '%s\n' "$SANDBOX_IMAGE" > "$IMAGE_READY_FILE"

# Remap localhost URLs to host.docker.internal for container access to host-local
# LLM servers such as Ollama, LM Studio, llama.cpp, MLX, LocalAI, or vLLM.
CONTAINER_BASE_URL="${BASE_URL//127.0.0.1/host.docker.internal}"
CONTAINER_BASE_URL="${CONTAINER_BASE_URL//localhost/host.docker.internal}"

SANDBOX_TASK="${TASK//$PROJECT_PATH/\/workspace}"
SANDBOX_TASK="${SANDBOX_TASK//$RUN_DIR/\/hoca-run}"
SANDBOX_USER="$(sandbox_resolve_user "$PROJECT_PATH")"
SANDBOX_HOME="$(sandbox_prepare_home "$RUN_DIR")"
NETWORK_MODE="$(sandbox_resolve_network_mode "$AGENT_ROLE" "$RUN_DIR")"
sandbox_record_network_policy "$AGENT_ROLE" "$RUN_DIR"
if [ "$NETWORK_MODE" = "offline" ] && printf '%s\n' "$CONTAINER_BASE_URL" | grep -q 'host\.docker\.internal'; then
  {
    echo "HOCA sandbox network_mode=offline cannot reach a host-local LLM endpoint."
    echo "Resolved LLM_BASE_URL uses host.docker.internal after localhost remapping."
    echo "Use an in-container LLM endpoint or opt into HOCA_NETWORK_MODE=package-install/full for this run."
  } | tee "$RUN_DIR/openhands-error.txt"
  exit 1
fi
record_timing_event() {
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "${HOCA_PYTHON:-python3}" -m hoca.run_timing event "$RUN_DIR" "$@" >/dev/null 2>&1 || true
}
SANDBOX_NETWORK_ARGS=()
while IFS= read -r _network_flag; do
  [ -n "$_network_flag" ] || continue
  SANDBOX_NETWORK_ARGS+=("$_network_flag")
done < <(sandbox_docker_network_args "$NETWORK_MODE")

# Optional dependency install inside the mounted worktree (no root package installs)
SETUP_SCRIPT="$RUN_DIR/sandbox-setup.sh"
cat > "$SETUP_SCRIPT" <<'SETUP_EOF'
#!/bin/bash
set -euo pipefail

SETUP_EOF
if [ "$NETWORK_MODE" != "offline" ]; then
  record_timing_event --type dependency_install --name sandbox-setup --role "$AGENT_ROLE"
  cat >> "$SETUP_SCRIPT" <<'SETUP_EOF'
if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then
  pnpm config set store-dir "${DEPS_STORE_DIR:-/hoca-deps-store}" >/dev/null 2>&1 || true
  INSTALL_CACHE_MARKER=".hoca-runtime/install-cache/sandbox-deps.sha256"
  INSTALL_CACHE_FINGERPRINT="$(python3 - <<'PY'
import hashlib
import subprocess
from pathlib import Path

digest = hashlib.sha256()
for name in ("pnpm-lock.yaml", "package.json"):
    path = Path(name)
    if path.is_file():
        digest.update(f"file:{name}\0".encode())
        digest.update(path.read_bytes())
        digest.update(b"\0")
for command in ("node", "pnpm", "python3"):
    try:
        result = subprocess.run(
            [command, "--version"], check=False, capture_output=True, text=True
        )
        version = result.stdout.strip() or result.stderr.strip() or str(result.returncode)
    except OSError:
        version = "missing"
    digest.update(f"{command}:{version}\0".encode())
print(digest.hexdigest())
PY
)"
  if [ "${HOCA_FORCE_INSTALL:-false}" = "true" ] || [ ! -d node_modules ] || [ "$(cat "$INSTALL_CACHE_MARKER" 2>/dev/null || true)" != "$INSTALL_CACHE_FINGERPRINT" ]; then
    pnpm install --frozen-lockfile 2>/dev/null || pnpm install 2>/dev/null || true
    mkdir -p "$(dirname "$INSTALL_CACHE_MARKER")"
    printf '%s\n' "$INSTALL_CACHE_FINGERPRINT" > "$INSTALL_CACHE_MARKER"
  else
    echo "Skipping pnpm install; install cache current."
  fi
elif [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then
  yarn install --frozen-lockfile 2>/dev/null || yarn install --immutable 2>/dev/null || yarn install 2>/dev/null || true
fi

SETUP_EOF
fi
cat >> "$SETUP_SCRIPT" <<'SETUP_EOF'
echo "Sandbox setup complete."
echo "  bun: $(command -v bun 2>/dev/null && bun --version || echo 'not available')"
echo "  node: $(command -v node 2>/dev/null && node --version || echo 'not available')"
echo "  pnpm: $(command -v pnpm 2>/dev/null || echo 'not available')"
echo "  yarn: $(command -v yarn 2>/dev/null || echo 'not available')"
echo "  openhands: $(command -v openhands 2>/dev/null && openhands --version 2>/dev/null || echo 'not available')"
SETUP_EOF
chmod +x "$SETUP_SCRIPT"

# Write task content to a file the container can read
TASK_FILE="$RUN_DIR/task-input.txt"
printf '%s' "$SANDBOX_TASK" > "$TASK_FILE"

echo "Starting sandboxed OpenHands execution..."
echo "  Container: $CONTAINER_NAME"
echo "  Image: $SANDBOX_IMAGE"
echo "  User: $SANDBOX_USER"
echo "  Network mode: $NETWORK_MODE"

# Start one run-scoped container with the project mounted (non-root; worktree
# owner uid:gid by default), then execute each phase via docker exec with fresh
# role/model env injection.
# Only allowlisted env vars are forwarded via explicit -e flags (see hoca/env_allowlist.py).
DOCKER_RUN_ARGS=(
  --name "$CONTAINER_NAME"
  --hostname "hoca-sandbox"
  --workdir /workspace
  -v "${PROJECT_PATH}:/workspace"
  "${GIT_DIR_MOUNTS[@]}"
  -v "${RUN_DIR}:/hoca-run"
  -v "${RUN_DIR}:/hoca-runs"
  -v "${RUN_DIR}:${RUN_DIR}"
  -v "${SANDBOX_HOME}:/home/hoca-sandbox"
  -v "${DEPS_STORE_HOST_DIR}:${DEPS_STORE_DIR}"
  -e "DEPS_STORE_DIR=${DEPS_STORE_DIR}"
  -e "OPENHANDS_SUPPRESS_BANNER=1"
  -e "HOME=/home/hoca-sandbox"
  --security-opt=no-new-privileges
  --cap-drop=ALL
  --memory="${HOCA_SANDBOX_MEMORY:-8g}"
  --pids-limit="${HOCA_SANDBOX_PIDS:-512}"
  --user "$SANDBOX_USER"
)
if [ "${#SANDBOX_NETWORK_ARGS[@]}" -gt 0 ]; then
  DOCKER_RUN_ARGS+=("${SANDBOX_NETWORK_ARGS[@]}")
else
  DOCKER_RUN_ARGS+=(--add-host=host.docker.internal:host-gateway)
fi

set +e
# The canonical per-role agent-loop markers are recorded by run-hoca-task.sh
# (worker-<engine> and reviewer-<mode>) and, for hermes mode, by the Hermes
# coordinator in worker_hermes/reviewer_hermes. Recording another agent_loop
# here double-counts the direct worker (whose sandbox shares the main run dir)
# while staying isolated for the reviewer/hermes paths, so the counter is left
# to those authoritative sources.
if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  record_timing_event --type container_start --name docker-run --role "$AGENT_ROLE"
  docker run -d \
    "${DOCKER_RUN_ARGS[@]}" \
    "$SANDBOX_IMAGE" \
    sleep infinity >/dev/null
  printf '%s\n' "$CONTAINER_NAME" > "$CONTAINER_NAME_FILE"
elif [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo false)" != "true" ]; then
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  record_timing_event --type container_start --name docker-run --role "$AGENT_ROLE"
  docker run -d \
    "${DOCKER_RUN_ARGS[@]}" \
    "$SANDBOX_IMAGE" \
    sleep infinity >/dev/null
  printf '%s\n' "$CONTAINER_NAME" > "$CONTAINER_NAME_FILE"
else
  printf '%s\n' "$CONTAINER_NAME" > "$CONTAINER_NAME_FILE"
fi

docker exec \
  -w /workspace \
  -e "LLM_MODEL=${MODEL}" \
  -e "LLM_BASE_URL=${CONTAINER_BASE_URL}" \
  -e "LLM_API_KEY=${API_KEY}" \
  -e "HOCA_AGENT_ROLE=${AGENT_ROLE}" \
  -e "DEPS_STORE_DIR=${DEPS_STORE_DIR}" \
  -e "OPENHANDS_SUPPRESS_BANNER=1" \
  -e "HOME=/home/hoca-sandbox" \
  "$CONTAINER_NAME" \
  bash -c "
    set -euo pipefail

    command -v openhands >/dev/null 2>&1 || {
      echo 'openhands command not found in sandbox image. Rebuild with scripts/sandbox-manager.sh build.' >&2
      exit 127
    }

    bash /hoca-run/sandbox-setup.sh

    # Trust the persistent OpenHands skills cache clone in the sandbox home.
    git config --global --add safe.directory /home/hoca-sandbox/.openhands/cache/skills/public-skills >/dev/null 2>&1 || true

    OPENHANDS_PERSISTENCE_DIR=/hoca-run/openhands-persistence
    export OPENHANDS_PERSISTENCE_DIR
    mkdir -p \"\$OPENHANDS_PERSISTENCE_DIR\"
    cat > /hoca-run/sitecustomize.py <<'PY'
try:
    from openhands_cli.stores.agent_store import AgentStore

    _hoca_original_build_agent_context = AgentStore._build_agent_context

    def _hoca_build_agent_context(self):
        context = _hoca_original_build_agent_context(self)
        return context.model_copy(
            update={
                'skills': [],
                'load_user_skills': False,
                'load_public_skills': False,
                'marketplace_path': None,
            }
        )

    AgentStore._build_agent_context = _hoca_build_agent_context
except Exception:
    pass
PY
    OPENHANDS_PYTHON=/opt/openhands-tools/openhands/bin/python
    if [ ! -x \"\$OPENHANDS_PYTHON\" ]; then
      OPENHANDS_PYTHON=python3
    fi
    SETTINGS_PATH=\"\$OPENHANDS_PERSISTENCE_DIR/agent_settings.json\"
    SETTINGS_FINGERPRINT_PATH=\"\$OPENHANDS_PERSISTENCE_DIR/agent_settings.fingerprint\"
    SETTINGS_FINGERPRINT=\$(\"\$OPENHANDS_PYTHON\" - \"${MODEL}\" \"${CONTAINER_BASE_URL}\" \"${API_KEY}\" <<'PY'
import hashlib
import sys

payload = \"\0\".join(sys.argv[1:4]).encode(\"utf-8\", \"surrogateescape\")
print(hashlib.sha256(payload).hexdigest())
PY
)
    if [ ! -f \"\$SETTINGS_PATH\" ] || [ \"\$(cat \"\$SETTINGS_FINGERPRINT_PATH\" 2>/dev/null || true)\" != \"\$SETTINGS_FINGERPRINT\" ]; then
      \"\$OPENHANDS_PYTHON\" - \"${MODEL}\" \"${CONTAINER_BASE_URL}\" \"${API_KEY}\" \"\$SETTINGS_PATH\" <<'PY'
import sys
from pathlib import Path

from openhands.sdk import LLM
from openhands.sdk.context.agent_context import AgentContext
from openhands_cli.utils import get_default_cli_agent

model, base_url, api_key, settings_path = sys.argv[1:5]
llm = LLM(
    model=model,
    base_url=base_url if base_url else None,
    api_key=api_key,
    usage_id=\"agent\",
    reasoning_effort=None,
    enable_encrypted_reasoning=False,
    extended_thinking_budget=None,
    timeout=600,
)
agent = get_default_cli_agent(llm).model_copy(
    update={
        'agent_context': AgentContext(
            load_user_skills=False,
            load_public_skills=False,
            marketplace_path=None,
        )
    }
)
Path(settings_path).write_text(agent.model_dump_json(), encoding=\"utf-8\")
PY
      printf '%s\n' \"\$SETTINGS_FINGERPRINT\" > \"\$SETTINGS_FINGERPRINT_PATH\"
    fi
    echo \"Using isolated OpenHands config: \$OPENHANDS_PERSISTENCE_DIR\"

    TASK_CONTENT=\$(cat /hoca-run/task-input.txt)

    PYTHONPATH=/hoca-run:\${PYTHONPATH:-} openhands --headless --task \"\$TASK_CONTENT\" --override-with-envs --json
  " 2>"$RUN_DIR/openhands-stderr.log" | \
  PYTHONPATH="$HOCA_ROOT" python3 -c "
import json
import subprocess
import sys
from pathlib import Path
from hoca.monitor import monitor_process_stream, MonitorResult

project_path = sys.argv[1]
run_dir = Path(sys.argv[2])
output_file = sys.argv[3]
timeout = int(sys.argv[4])
stall = int(sys.argv[5])
actor_role = sys.argv[6]
container_name = sys.argv[7]

cancelled = False

def stop_container(reason: str) -> None:
    global cancelled
    if cancelled:
        return
    cancelled = True
    subprocess.run(
        ['docker', 'stop', container_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

with open(output_file, 'w') as out_f:
    result = monitor_process_stream(
        sys.stdin,
        project_path=project_path,
        run_dir=run_dir,
        timeout_seconds=timeout,
        stall_seconds=stall,
        output_file=out_f,
        on_cancel=stop_container,
        actor_role=actor_role,
    )

with open(str(run_dir / 'openhands-exit-code.txt'), 'w') as f:
    f.write(str(result.exit_code) + '\n')

with open(str(run_dir / 'monitor-result.json'), 'w') as f:
    json.dump(result.to_dict(), f, indent=2, sort_keys=True)
    f.write('\n')

if result.stop_reason != 'completed':
    print(f'OpenHands stopped by monitor: {result.stop_reason}', file=sys.stderr)
    for e in result.events:
        if e.kind not in ('info', 'exit'):
            print(f'  [{e.kind}] {e.message}', file=sys.stderr)
    sys.exit(1)

sys.exit(result.exit_code)
" "$PROJECT_PATH" "$RUN_DIR" "$RUN_DIR/openhands-output.jsonl" "$TIMEOUT" "$STALL" "$AGENT_ROLE" "$CONTAINER_NAME"
EXIT_CODE=$?
set -e

if [ "$EXIT_CODE" -ne 0 ]; then
  if [ -f "$RUN_DIR/monitor-stop.json" ]; then
    echo "OpenHands was stopped by the safety monitor."
    cat "$RUN_DIR/monitor-stop.json"
  else
    echo "OpenHands failed with exit code $EXIT_CODE."
  fi
  echo "Logs: $RUN_DIR/"
  exit "$EXIT_CODE"
fi

if grep -q '"kind": "ConversationErrorEvent"' "$RUN_DIR/openhands-output.jsonl"; then
  echo "OpenHands reported a conversation error event." | tee "$RUN_DIR/openhands-error.txt"
  echo "Logs: $RUN_DIR/"
  exit 1
fi

echo "OpenHands (sandboxed) completed successfully."
