#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: kanban-run.sh /path/to/project \"task\""
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"

if ! command -v hermes >/dev/null 2>&1; then
  echo "Error: hermes is not installed."
  echo "Install Hermes Agent to use Kanban features: https://github.com/NousResearch/hermes-agent"
  exit 1
fi

if ! hermes kanban -h >/dev/null 2>&1; then
  echo "Error: Hermes Kanban is not available in the installed version of Hermes."
  echo "Upgrade Hermes Agent to a version that supports Kanban boards."
  exit 1
fi

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH"
  exit 1
fi

REPO_SLUG="$(basename "$PROJECT_PATH")"
BOARD_NAME="hoca:${REPO_SLUG}"

echo "Creating HOCA Kanban task on board: $BOARD_NAME"
echo "Task: $TASK"
echo ""

TASK_ID="hoca-$(date -u +%Y%m%dT%H%M%SZ)"

hermes kanban add "$BOARD_NAME" \
  --title "$TASK" \
  --assignee hoca-manager \
  --status triage \
  --metadata "task_id=$TASK_ID" \
  2>&1 || {
    echo ""
    echo "Failed to create Kanban task."
    echo "Ensure the board exists: hoca kanban-init $PROJECT_PATH"
    exit 1
  }

echo ""
echo "Kanban task created: $TASK_ID"
echo "Board: $BOARD_NAME"
echo "Assignee: hoca-manager"
echo ""
echo "The manager profile will pick up this task from the board."
echo "Use 'hoca kanban-watch' to monitor progress."
