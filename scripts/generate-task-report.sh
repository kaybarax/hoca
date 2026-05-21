#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: generate-task-report.sh /path/to/project /path/to/run-dir" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
RUN_DIR="$(mkdir -p "$2" && cd "$2" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_PATH"

PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.task_report "$PROJECT_PATH" "$RUN_DIR"
