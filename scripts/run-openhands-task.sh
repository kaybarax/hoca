#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: run-openhands-task.sh /path/to/project \"task\" /path/to/run-dir"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$(mkdir -p "$3" && cd "$3" && pwd)"

cd "$PROJECT_PATH"
mkdir -p "$RUN_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SELECTED_MODEL="$("$SCRIPT_DIR/select-model.sh")"
MODEL="${LLM_MODEL:-ollama/$SELECTED_MODEL}"
BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:11434}"
API_KEY="${LLM_API_KEY:-ollama}"

TIMEOUT="${HOCA_OPENHANDS_TIMEOUT:-600}"
STALL="${HOCA_OPENHANDS_STALL:-300}"

echo "Running OpenHands with:"
echo "  MODEL=$MODEL"
echo "  BASE_URL=$BASE_URL"
echo "  PROJECT_PATH=$PROJECT_PATH"
echo "  TIMEOUT=${TIMEOUT}s"
echo "  STALL=${STALL}s"

if ! command -v openhands >/dev/null 2>&1; then
  echo "openhands command not found." | tee "$RUN_DIR/openhands-error.txt"
  exit 1
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
oh_args = sys.argv[6:]

env_override = dict(__import__('os').environ)
env_override['LLM_MODEL'] = '${MODEL}'
env_override['LLM_BASE_URL'] = '${BASE_URL}'
env_override['LLM_API_KEY'] = '${API_KEY}'

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
                yield line

    tee = TeeStdout(proc, out_f)
    proc.stdout = tee

    result = monitor_process(
        proc,
        project_path=project_path,
        run_dir=run_dir,
        timeout_seconds=timeout,
        stall_seconds=stall,
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
" "$PROJECT_PATH" "$RUN_DIR" "$OUTPUT_FILE" "$TIMEOUT" "$STALL" "${OH_FLAGS[@]}"
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

echo "OpenHands completed successfully."
