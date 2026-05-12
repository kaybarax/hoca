#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELECTED_MODEL="$("$SCRIPT_DIR/select-model.sh")"

AIDER_MODEL="${AIDER_MODEL:-ollama_chat/$SELECTED_MODEL}"

echo "HOCA Aider review skeleton"
echo "Selected model: $AIDER_MODEL"
