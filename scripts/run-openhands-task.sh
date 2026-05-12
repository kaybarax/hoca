#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELECTED_MODEL="$("$SCRIPT_DIR/select-model.sh")"

export LLM_MODEL="${LLM_MODEL:-ollama/$SELECTED_MODEL}"

echo "HOCA OpenHands runner skeleton"
echo "Selected model: $LLM_MODEL"
