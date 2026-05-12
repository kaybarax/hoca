#!/usr/bin/env bash
set -euo pipefail

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama CLI is required to select a HOCA-compatible model." >&2
  exit 1
fi

MODEL_LIST="$(ollama list | awk 'NR > 1 {print $1}')"

model_exists() {
  local candidate="$1"
  printf '%s\n' "$MODEL_LIST" | grep -qx "$candidate"
}

try_model() {
  local candidate="$1"
  if [[ -n "$candidate" ]] && model_exists "$candidate"; then
    echo "$candidate"
    exit 0
  fi
}

try_model "${OLLAMA_MODEL:-}"
try_model "qwen-32b-pro"
try_model "qwen-14b-pro"
try_model "qwen-7b-pro"

echo "No HOCA-compatible Ollama model found. Build one with scripts/install.sh or create qwen-32b-pro, qwen-14b-pro, or qwen-7b-pro." >&2
exit 1
