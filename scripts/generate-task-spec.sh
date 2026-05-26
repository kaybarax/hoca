#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: generate-task-spec.sh /path/to/project "raw task" /path/to/run-dir [options]

Gather repository metadata and project instructions, then write task-spec.json
for a HOCA profile-backed run.

Options:
  --issue-id ID           Optional linked issue id
  --run-id ID             Run id (default: basename of run-dir)
  --base-branch BRANCH    Base branch for the task (default: current branch)
  --task-branch BRANCH    Working branch name (default: derived from task/issue)
  --max-total-rounds N    Total worker/review rounds cap (default: from HOCA config)
  -h, --help              Show this help message
EOF
}

if [ "$#" -lt 3 ]; then
  usage
  exit 1
fi

PROJECT_PATH="$1"
TASK="$2"
RUN_DIR="$3"
shift 3

ISSUE_ID=""
RUN_ID=""
BASE_BRANCH=""
TASK_BRANCH=""
MAX_TOTAL_ROUNDS=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --issue-id)
      ISSUE_ID="${2:-}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --base-branch)
      BASE_BRANCH="${2:-}"
      shift 2
      ;;
    --task-branch)
      TASK_BRANCH="${2:-}"
      shift 2
      ;;
    --max-total-rounds)
      MAX_TOTAL_ROUNDS="${2:-}"
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

if [ ! -d "$PROJECT_PATH" ]; then
  echo "Project path does not exist: $PROJECT_PATH" >&2
  exit 1
fi

if [ -z "$TASK" ] || [ -z "${TASK//[[:space:]]/}" ]; then
  echo "Task text must not be empty." >&2
  exit 1
fi

PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! git -C "$PROJECT_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH" >&2
  exit 1
fi

PYTHON_ARGS=(
  -m hoca.task_spec
  "$PROJECT_PATH"
  "$TASK"
  "$RUN_DIR"
)

if [ -n "$ISSUE_ID" ]; then
  PYTHON_ARGS+=(--issue-id "$ISSUE_ID")
fi
if [ -n "$RUN_ID" ]; then
  PYTHON_ARGS+=(--run-id "$RUN_ID")
fi
if [ -n "$BASE_BRANCH" ]; then
  PYTHON_ARGS+=(--base-branch "$BASE_BRANCH")
fi
if [ -n "$TASK_BRANCH" ]; then
  PYTHON_ARGS+=(--task-branch "$TASK_BRANCH")
fi
if [ -n "$MAX_TOTAL_ROUNDS" ]; then
  PYTHON_ARGS+=(--max-total-rounds "$MAX_TOTAL_ROUNDS")
fi

PYTHON_BIN="${HOCA_PYTHON:-python3}"
PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" "${PYTHON_ARGS[@]}"
