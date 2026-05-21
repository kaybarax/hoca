#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: kanban-watch.sh /path/to/project"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"

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

REPO_SLUG="$(basename "$PROJECT_PATH")"
BOARD_NAME="hoca:${REPO_SLUG}"

echo "HOCA Kanban Board: $BOARD_NAME"
echo "Project: $PROJECT_PATH"
echo ""

hermes kanban list "$BOARD_NAME" 2>&1 || {
  echo ""
  echo "Failed to list Kanban tasks."
  echo "Ensure the board exists: hoca kanban-init $PROJECT_PATH"
  exit 1
}
