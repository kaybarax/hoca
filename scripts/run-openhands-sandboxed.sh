#!/usr/bin/env bash
set -euo pipefail

# Runs OpenHands inside a Docker sandbox container.
# The container has bun, node, pnpm, git pre-installed and network access.
# The project is mounted at /workspace and HOCA's monitor watches stdout.

if [ "$#" -lt 8 ]; then
  echo "Usage: run-openhands-sandboxed.sh <project-path> <task> <run-dir> <model> <base-url> <api-key> <timeout> <stall>"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SANDBOX_IMAGE="${HOCA_SANDBOX_IMAGE:-hoca-sandbox:latest}"
RUN_ID="$(basename "$RUN_DIR")"
CONTAINER_NAME="hoca-worker-${RUN_ID}"

# Ensure sandbox image exists
if ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  echo "Building sandbox image..."
  docker build -t "$SANDBOX_IMAGE" -f "$HOCA_ROOT/docker/Dockerfile.sandbox" "$HOCA_ROOT/docker"
fi

# Remap localhost URLs to host.docker.internal for container access to host Ollama
CONTAINER_BASE_URL="${BASE_URL//127.0.0.1/host.docker.internal}"
CONTAINER_BASE_URL="${CONTAINER_BASE_URL//localhost/host.docker.internal}"

# Install openhands inside container needs pip; use a richer base with Python
# Actually, we need OpenHands CLI inside the container. Let's use a setup approach.
SETUP_SCRIPT="$RUN_DIR/sandbox-setup.sh"
cat > "$SETUP_SCRIPT" <<'SETUP_EOF'
#!/bin/bash
set -euo pipefail

# Install Python and pip if not available
if ! command -v python3 >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv >/dev/null 2>&1
fi

# Install OpenHands CLI in a venv
if ! command -v openhands >/dev/null 2>&1; then
  python3 -m venv /tmp/openhands-venv
  /tmp/openhands-venv/bin/pip install -q openhands-ai 2>/dev/null
  export PATH="/tmp/openhands-venv/bin:$PATH"
fi

# Run pnpm install if lockfile exists
if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then
  pnpm install --frozen-lockfile 2>/dev/null || pnpm install 2>/dev/null || true
fi

echo "Sandbox setup complete."
echo "  bun: $(command -v bun 2>/dev/null && bun --version || echo 'not available')"
echo "  node: $(command -v node 2>/dev/null && node --version || echo 'not available')"
echo "  pnpm: $(command -v pnpm 2>/dev/null && pnpm --version || echo 'not available')"
echo "  openhands: $(command -v openhands 2>/dev/null && openhands --version 2>/dev/null || echo 'not available')"
SETUP_EOF
chmod +x "$SETUP_SCRIPT"

# Create the entrypoint script that runs inside the container
ENTRYPOINT_SCRIPT="$RUN_DIR/sandbox-entrypoint.sh"
cat > "$ENTRYPOINT_SCRIPT" <<ENTRY_EOF
#!/bin/bash
set -euo pipefail

export LLM_MODEL="$MODEL"
export LLM_BASE_URL="$CONTAINER_BASE_URL"
export LLM_API_KEY="$API_KEY"
export OPENHANDS_SUPPRESS_BANNER=1
export PATH="/tmp/openhands-venv/bin:\$PATH"

# Run setup
bash /workspace/.hoca-runtime/runs/$RUN_ID/sandbox-setup.sh

# Run OpenHands in headless mode
OH_FLAGS=(--headless --task "\$TASK_CONTENT" --override-with-envs --json)

openhands "\${OH_FLAGS[@]}"
ENTRY_EOF
chmod +x "$ENTRYPOINT_SCRIPT"

# Write task content to a file the container can read
TASK_FILE="$RUN_DIR/task-input.txt"
printf '%s' "$TASK" > "$TASK_FILE"

# Cleanup on exit
cleanup_container() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup_container EXIT

echo "Starting sandboxed OpenHands execution..."
echo "  Container: $CONTAINER_NAME"
echo "  Image: $SANDBOX_IMAGE"

# Run the container with the project mounted
set +e
docker run \
  --name "$CONTAINER_NAME" \
  --hostname "hoca-sandbox" \
  --workdir /workspace \
  -v "${PROJECT_PATH}:/workspace" \
  -v "${RUN_DIR}:/workspace/.hoca-runtime/runs/${RUN_ID}" \
  -e "LLM_MODEL=${MODEL}" \
  -e "LLM_BASE_URL=${CONTAINER_BASE_URL}" \
  -e "LLM_API_KEY=${API_KEY}" \
  -e "GITHUB_TOKEN=${GITHUB_TOKEN:-}" \
  -e "OPENHANDS_SUPPRESS_BANNER=1" \
  -e "HOME=/home/worker" \
  --add-host=host.docker.internal:host-gateway \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  --cap-add=NET_RAW \
  --memory="${HOCA_SANDBOX_MEMORY:-8g}" \
  --pids-limit="${HOCA_SANDBOX_PIDS:-512}" \
  --user root \
  "$SANDBOX_IMAGE" \
  bash -c "
    set -euo pipefail
    export PATH=\"/tmp/openhands-venv/bin:\$PATH\"

    # Setup phase
    bash /workspace/.hoca-runtime/runs/${RUN_ID}/sandbox-setup.sh

    # Read task
    TASK_CONTENT=\$(cat /workspace/.hoca-runtime/runs/${RUN_ID}/task-input.txt)

    # Run OpenHands
    openhands --headless --task \"\$TASK_CONTENT\" --override-with-envs --json
  " 2>"$RUN_DIR/openhands-stderr.log" | \
  PYTHONPATH="$HOCA_ROOT" python3 -c "
import json
import sys
from pathlib import Path
from hoca.monitor import monitor_process_stream, MonitorResult

project_path = sys.argv[1]
run_dir = Path(sys.argv[2])
output_file = sys.argv[3]
timeout = int(sys.argv[4])
stall = int(sys.argv[5])

with open(output_file, 'w') as out_f:
    result = monitor_process_stream(
        sys.stdin,
        project_path=project_path,
        run_dir=run_dir,
        timeout_seconds=timeout,
        stall_seconds=stall,
        output_file=out_f,
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
" "$PROJECT_PATH" "$RUN_DIR" "$RUN_DIR/openhands-output.jsonl" "$TIMEOUT" "$STALL"
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

echo "OpenHands (sandboxed) completed successfully."
