#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: kanban-init.sh /path/to/project"
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

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH"
  exit 1
fi

REPO_SLUG="$(
  basename "$PROJECT_PATH" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+|-+$//g'
)"
BOARD_NAME="hoca:${REPO_SLUG}"

echo "Initializing HOCA Kanban board: $BOARD_NAME"
echo "Project: $PROJECT_PATH"
echo ""

hermes kanban boards create "$BOARD_NAME" \
  --name "HOCA: $REPO_SLUG" \
  --description "HOCA engineering pipeline for $REPO_SLUG" \
  2>&1 || {
    echo ""
    echo "Kanban board creation failed."
    echo "The board may already exist, or the Hermes Kanban backend may need configuration."
    exit 1
  }

echo ""
echo "HOCA Kanban board initialized: $BOARD_NAME"
echo ""
echo "Use 'hoca kanban-run' to create tasks on this board."
echo "Use 'hoca kanban-watch' to monitor board status."
