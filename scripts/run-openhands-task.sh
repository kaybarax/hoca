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

SELECTED_MODEL="$("$SCRIPT_DIR/select-model.sh")"
MODEL="${LLM_MODEL:-ollama/$SELECTED_MODEL}"
BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:11434}"
API_KEY="${LLM_API_KEY:-ollama}"

echo "Running OpenHands with:"
echo "  MODEL=$MODEL"
echo "  BASE_URL=$BASE_URL"
echo "  PROJECT_PATH=$PROJECT_PATH"

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

if [ "$USE_JSON" = true ]; then
  OUTPUT_FILE="$RUN_DIR/openhands-output.jsonl"
else
  OUTPUT_FILE="$RUN_DIR/openhands-output.log"
fi

set +e
LLM_MODEL="$MODEL" \
LLM_BASE_URL="$BASE_URL" \
LLM_API_KEY="$API_KEY" \
openhands "${OH_FLAGS[@]}" \
  > "$OUTPUT_FILE" 2> "$RUN_DIR/openhands-stderr.log"
EXIT_CODE=$?
set -e

echo "$EXIT_CODE" > "$RUN_DIR/openhands-exit-code.txt"

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "OpenHands failed with exit code $EXIT_CODE."
  echo "Logs: $RUN_DIR/openhands-stderr.log"
  exit "$EXIT_CODE"
fi

echo "OpenHands completed successfully."
