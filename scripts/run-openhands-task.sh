#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: run-openhands-task.sh /path/to/project \"task\" /path/to/run-dir"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$(mkdir -p "$3" && cd "$3" && pwd)"

if [ -f "$TASK" ]; then
  TASK="$(cat "$TASK")"
fi

cd "$PROJECT_PATH"
mkdir -p "$RUN_DIR"

TASK="$(cat <<EOF
HOCA execution root: $PROJECT_PATH

This is the only repository root you may read, write, inspect, or run commands in.
If the task text, task spec, or validation commands mention another absolute
checkout path, treat that path as reference metadata and rewrite the command to
run from this execution root instead. Do not cd to the original checkout or any
parent/sibling repository.

$TASK
EOF
)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ROLE="${HOCA_AGENT_ROLE:-worker}"

case "$AGENT_ROLE" in
  worker|reviewer)
    unset GITHUB_TOKEN 2>/dev/null || true
    ;;
esac

# Drop pooled credentials so only the active role's LLM_* values are forwarded.
for _pool_index in 1 2 3 4 5; do
  unset "HOCA_MODEL_${_pool_index}_API_KEY" 2>/dev/null || true
done

if [ "${HOCA_SKIP_ROLE_MODEL_RESOLUTION:-}" != "true" ]; then
  if [ "${HOCA_LOCK_ROLE_MODEL:-}" = "true" ]; then
    case "$AGENT_ROLE" in
      worker|reviewer)
        unset HOCA_REQUESTED_MODEL HOCA_CLI_MODEL_OVERRIDE OLLAMA_MODEL 2>/dev/null || true
        unset LLM_MODEL LLM_BASE_URL LLM_API_KEY 2>/dev/null || true
        ;;
    esac
  fi
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/resolve-role-model-env.sh" "$AGENT_ROLE"
fi

case "${LLM_MODEL:-}" in
  deepseek/*|gemini/*|anthropic/*|together_ai/*|openrouter/*)
    MODEL="${LLM_MODEL}"
    BASE_URL="${LLM_BASE_URL:-}"
    API_KEY="${LLM_API_KEY:?LLM_API_KEY is required for cloud providers}"
    ;;
  openai/*)
    MODEL="${LLM_MODEL}"
    BASE_URL="${LLM_BASE_URL:-http://localhost:1234/v1}"
    API_KEY="${LLM_API_KEY:-lm-studio}"
    ;;
  ollama/*)
    MODEL="${LLM_MODEL}"
    BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:11434}"
    API_KEY="${LLM_API_KEY:-ollama}"
    ;;
  "")
    SELECTED_MODEL="$("$SCRIPT_DIR/select-model.sh")"
    MODEL="ollama/$SELECTED_MODEL"
    BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:11434}"
    API_KEY="${LLM_API_KEY:-ollama}"
    ;;
  *)
    MODEL="${LLM_MODEL}"
    BASE_URL="${LLM_BASE_URL:-}"
    API_KEY="${LLM_API_KEY:?LLM_API_KEY is required}"
    ;;
esac

TIMEOUT="${HOCA_OPENHANDS_TIMEOUT:-600}"
STALL="${HOCA_OPENHANDS_STALL:-300}"
USE_SANDBOX="${HOCA_USE_SANDBOX:-true}"

warn_host_execution() {
  local reason="$1"
  {
    echo "[WARN] HOCA host execution (higher risk): $reason"
    echo "[WARN] OpenHands will run on the host with access to the project worktree and your shell environment."
    echo "[WARN] Prefer HOCA_USE_SANDBOX=true (default) and a running Docker daemon for worker/reviewer rounds."
    echo "[WARN] To keep host execution, set HOCA_USE_SANDBOX=false explicitly in .env or the environment."
  } | tee -a "$RUN_DIR/host-execution-warning.txt" >&2
}

echo "Running OpenHands with:"
echo "  MODEL=$MODEL"
echo "  BASE_URL=$BASE_URL"
echo "  PROJECT_PATH=$PROJECT_PATH"
echo "  TIMEOUT=${TIMEOUT}s"
echo "  STALL=${STALL}s"
echo "  SANDBOX=$USE_SANDBOX"
echo "  ROLE=$AGENT_ROLE"
if [ "$USE_SANDBOX" = "true" ] && [ -x "$SCRIPT_DIR/run-openhands-sandboxed.sh" ]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    # shellcheck source=scripts/sandbox-docker-env.sh
    source "$SCRIPT_DIR/sandbox-docker-env.sh"
    SANDBOX_NETWORK_MODE="$(sandbox_resolve_network_mode "$AGENT_ROLE" "$RUN_DIR")"
    echo "  NETWORK_MODE=$SANDBOX_NETWORK_MODE"
  fi
fi

cat > "$RUN_DIR/agent-role-policy.txt" <<EOF
role: $AGENT_ROLE
manager_owned_git_lifecycle: true
forbidden_for_worker_and_reviewer:
- git add
- git commit
- git push
- git merge
- gh pr create
- gh pr merge
EOF

# --- Sandbox mode: run everything inside a Docker container ---
if [ "$USE_SANDBOX" = "true" ]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    SANDBOX_SCRIPT="$SCRIPT_DIR/run-openhands-sandboxed.sh"
    if [ -x "$SANDBOX_SCRIPT" ]; then
      exec "$SANDBOX_SCRIPT" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "$MODEL" "$BASE_URL" "$API_KEY" "$TIMEOUT" "$STALL" "$AGENT_ROLE"
    fi
    warn_host_execution "HOCA_USE_SANDBOX=true but run-openhands-sandboxed.sh not found; falling back to host execution."
  elif ! command -v docker >/dev/null 2>&1; then
    warn_host_execution "HOCA_USE_SANDBOX=true but docker is not installed; falling back to host execution."
  else
    warn_host_execution "HOCA_USE_SANDBOX=true but Docker daemon is not running; falling back to host execution."
  fi
else
  warn_host_execution "HOCA_USE_SANDBOX is not enabled (explicit host-local opt-in)."
fi

if ! command -v openhands >/dev/null 2>&1; then
  echo "openhands command not found." | tee "$RUN_DIR/openhands-error.txt"
  exit 1
fi

OPENHANDS_BIN="$(command -v openhands)"
OPENHANDS_SHEBANG="$(head -n 1 "$OPENHANDS_BIN" 2>/dev/null || true)"
OPENHANDS_PYTHON=""
case "$OPENHANDS_SHEBANG" in
  "#!"*python*)
    OPENHANDS_PYTHON="${OPENHANDS_SHEBANG#\#!}"
    ;;
esac

if [ -n "$OPENHANDS_PYTHON" ] && [ -x "$OPENHANDS_PYTHON" ]; then
  OPENHANDS_PERSISTENCE_DIR="$RUN_DIR/openhands-persistence"
  export OPENHANDS_PERSISTENCE_DIR
  mkdir -p "$OPENHANDS_PERSISTENCE_DIR"
  "$OPENHANDS_PYTHON" - "$MODEL" "$BASE_URL" "$API_KEY" "$OPENHANDS_PERSISTENCE_DIR/agent_settings.json" <<'PY'
import sys
from pathlib import Path

from openhands.sdk import LLM
from openhands_cli.utils import get_default_cli_agent

model, base_url, api_key, settings_path = sys.argv[1:5]
llm = LLM(
    model=model,
    base_url=base_url if base_url else None,
    api_key=api_key,
    usage_id="agent",
    reasoning_effort=None,
    enable_encrypted_reasoning=False,
    extended_thinking_budget=None,
    timeout=600,
)
agent = get_default_cli_agent(llm)
Path(settings_path).write_text(agent.model_dump_json(), encoding="utf-8")
PY
  echo "Using isolated OpenHands config: $OPENHANDS_PERSISTENCE_DIR"
else
  echo "Could not create isolated OpenHands config; using OpenHands defaults."
fi

OH_HELP="$(openhands --help 2>&1 || true)"

if ! printf '%s\n' "$OH_HELP" | grep -q -- "--headless"; then
  echo "OpenHands CLI does not support --headless. Cannot proceed." | tee "$RUN_DIR/openhands-error.txt"
  exit 1
fi

if ! printf '%s\n' "$OH_HELP" | grep -q -- "--task"; then
  echo "OpenHands CLI does not support --task. Cannot proceed." | tee "$RUN_DIR/openhands-error.txt"
  exit 1
fi

OH_FLAGS=(--headless --task "$TASK")

if printf '%s\n' "$OH_HELP" | grep -q -- "--override-with-envs"; then
  OH_FLAGS+=(--override-with-envs)
fi

USE_JSON=false
if printf '%s\n' "$OH_HELP" | grep -q -- "--json"; then
  OH_FLAGS+=(--json)
  USE_JSON=true
fi

OH_CAPS="headless,task"
if printf '%s\n' "$OH_HELP" | grep -q -- "--override-with-envs"; then
  OH_CAPS="$OH_CAPS,override-with-envs"
fi
if [ "$USE_JSON" = true ]; then
  OH_CAPS="$OH_CAPS,json"
fi
if printf '%s\n' "$OH_HELP" | grep -q -- "--enable-browsing"; then
  OH_CAPS="$OH_CAPS,enable-browsing"
fi
printf '%s\n' "$OH_CAPS" > "$RUN_DIR/openhands-capabilities.txt"

if [ "$USE_JSON" = true ]; then
  OUTPUT_FILE="$RUN_DIR/openhands-output.jsonl"
else
  OUTPUT_FILE="$RUN_DIR/openhands-output.log"
fi

echo "Starting OpenHands with monitoring..."

set +e
PYTHONPATH="$HOCA_ROOT" python3 -c "
import json
import subprocess
import sys
from pathlib import Path
from hoca.monitor import monitor_process, MonitorResult

project_path = sys.argv[1]
run_dir = Path(sys.argv[2])
output_file = sys.argv[3]
timeout = int(sys.argv[4])
stall = int(sys.argv[5])
actor_role = sys.argv[6]
oh_args = sys.argv[7:]

env_override = dict(__import__('os').environ)
if '${AGENT_ROLE}' in ('worker', 'reviewer'):
    env_override.pop('GITHUB_TOKEN', None)
env_override['LLM_MODEL'] = '${MODEL}'
if '${BASE_URL}':
    env_override['LLM_BASE_URL'] = '${BASE_URL}'
env_override['LLM_API_KEY'] = '${API_KEY}'
env_override['CI'] = 'true'

with open(output_file, 'w') as out_f:
    proc = subprocess.Popen(
        ['openhands'] + list(oh_args),
        stdout=subprocess.PIPE,
        stderr=open(str(run_dir / 'openhands-stderr.log'), 'w'),
        text=True,
        env=env_override,
    )

    class TeeStdout:
        def __init__(self, proc, out_file):
            self._proc = proc
            self._out_file = out_file
            self._real_stdout = proc.stdout

        def __iter__(self):
            for line in self._real_stdout:
                self._out_file.write(line)
                self._out_file.flush()
                print(line, end='', flush=True)
                yield line

    tee = TeeStdout(proc, out_f)
    proc.stdout = tee

    result = monitor_process(
        proc,
        project_path=project_path,
        run_dir=run_dir,
        timeout_seconds=timeout,
        stall_seconds=stall,
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
" "$PROJECT_PATH" "$RUN_DIR" "$OUTPUT_FILE" "$TIMEOUT" "$STALL" "$AGENT_ROLE" "${OH_FLAGS[@]}"
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

if grep -q '"kind": "ConversationErrorEvent"' "$OUTPUT_FILE"; then
  echo "OpenHands reported a conversation error event." | tee "$RUN_DIR/openhands-error.txt"
  echo "Logs: $RUN_DIR/"
  exit 1
fi

echo "OpenHands completed successfully."
