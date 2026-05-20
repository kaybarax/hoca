#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-worker-hermes.sh /path/to/project /path/to/task-spec.json /path/to/run-dir ROUND [options]

Run a worker attempt through the hoca-worker Hermes profile when
HOCA_USE_HERMES_PROFILES=true, or fall back to run-openhands-task.sh in legacy mode.

Worker model selection is resolved in hoca.worker_hermes (model pool role worker, or
legacy LLM_MODEL). run-hoca-task.sh also sources resolve-role-model-env.sh worker
before each implementation phase when the model pool is active.

Options:
  --repair-brief PATH   Optional repair brief file for rounds after the first attempt
  -h, --help            Show this help message
EOF
}

if [ "$#" -lt 4 ]; then
  usage
  exit 1
fi

PROJECT_PATH="$1"
TASK_SPEC_PATH="$2"
RUN_DIR="$3"
ROUND="$4"
shift 4

REPAIR_BRIEF=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repair-brief)
      REPAIR_BRIEF="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! [[ "$ROUND" =~ ^[0-9]+$ ]] || [ "$ROUND" -lt 1 ]; then
  echo "Round must be an integer greater than or equal to 1." >&2
  exit 1
fi

if [ ! -d "$PROJECT_PATH" ]; then
  echo "Project path does not exist: $PROJECT_PATH" >&2
  exit 1
fi

if [ ! -f "$TASK_SPEC_PATH" ]; then
  echo "Task spec not found: $TASK_SPEC_PATH" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$(mkdir -p "$RUN_DIR" && cd "$RUN_DIR" && pwd)"
TASK_SPEC_PATH="$(cd "$(dirname "$TASK_SPEC_PATH")" && pwd)/$(basename "$TASK_SPEC_PATH")"

if ! git -C "$PROJECT_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH" >&2
  exit 1
fi

PYTHON_ARGS=(
  -m hoca.worker_hermes
  "$PROJECT_PATH"
  "$TASK_SPEC_PATH"
  "$RUN_DIR"
  "$ROUND"
)

if [ -n "$REPAIR_BRIEF" ]; then
  if [ ! -f "$REPAIR_BRIEF" ]; then
    echo "Repair brief not found: $REPAIR_BRIEF" >&2
    exit 1
  fi
  PYTHON_ARGS+=(--repair-brief "$REPAIR_BRIEF")
fi

PYTHON_BIN="${HOCA_PYTHON:-python3}"
PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "${PYTHON_ARGS[@]}"
