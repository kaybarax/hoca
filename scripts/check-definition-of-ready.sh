#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: check-definition-of-ready.sh /path/to/project \"task\" [--issue-id ID] [--run-dir /path/to/run-dir]"
  exit 1
fi

PROJECT_PATH="$1"
TASK="$2"
shift 2

ISSUE_ID=""
RUN_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --issue-id)
      ISSUE_ID="${2:-}"
      shift 2
      ;;
    --run-dir)
      RUN_DIR="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_ARGS=(
  -m hoca.definition_of_ready
  "$PROJECT_PATH"
  "$TASK"
)

if [ -n "$ISSUE_ID" ]; then
  PYTHON_ARGS+=(--issue-id "$ISSUE_ID")
fi
if [ -n "$RUN_DIR" ]; then
  PYTHON_ARGS+=(--run-dir "$RUN_DIR")
fi

PYTHON_BIN="${HOCA_PYTHON:-python3}"
PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "${PYTHON_ARGS[@]}"
