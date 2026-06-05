#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  run-lane-agent.sh --project-path /path/to/repo --task "description" \
    --lane-id lane-id [--task-id task-id] [--project-id project-id]
EOF
}

PROJECT_PATH=""
TASK=""
WORKTREE_PATH=""
LANE_ID=""
TASK_ID=""
PROJECT_ID=""
RUN_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project-path)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      PROJECT_PATH="$2"
      shift 2
      ;;
    --task)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      TASK="$2"
      shift 2
      ;;
    --worktree-path)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      WORKTREE_PATH="$2"
      shift 2
      ;;
    --lane-id)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      LANE_ID="$2"
      shift 2
      ;;
    --task-id)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      TASK_ID="$2"
      shift 2
      ;;
    --project-id)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      PROJECT_ID="$2"
      shift 2
      ;;
    --run-dir)
      if [ "$#" -lt 2 ]; then
        usage
        exit 1
      fi
      RUN_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ -z "$PROJECT_PATH" ] || [ -z "$TASK" ] || [ -z "$LANE_ID" ]; then
  usage
  exit 1
fi

if [ ! -d "$PROJECT_PATH/.git" ]; then
  echo "Project path is not a git repository: $PROJECT_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_RUN_HERE_SCRIPT="${SCRIPT_DIR}/run-hoca-task.sh"
if [ ! -x "$HOCA_RUN_HERE_SCRIPT" ]; then
  echo "Missing runtime script: $HOCA_RUN_HERE_SCRIPT" >&2
  exit 1
fi

WORKTREE_PATH="${WORKTREE_PATH:-$PROJECT_PATH}"
export HOCA_LANE_ID="$LANE_ID"
export HOCA_TASK_ID="$TASK_ID"
export HOCA_PROJECT_ID="$PROJECT_ID"
export HOCA_WORKTREE_PATH="$WORKTREE_PATH"
export HOCA_ADAPTER_SESSION_DIR="$RUN_DIR"

"$HOCA_RUN_HERE_SCRIPT" "$PROJECT_PATH" "$TASK"
