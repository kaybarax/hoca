#!/usr/bin/env bash
set -euo pipefail

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama CLI is required to select a HOCA-compatible model. Install Ollama and run: ollama serve" >&2
  exit 1
fi

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"

if command -v curl >/dev/null 2>&1; then
  if ! curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    echo "Ollama server is not reachable at $OLLAMA_BASE_URL. Start it with: ollama serve" >&2
    exit 1
  fi
fi

if ! MODEL_LIST="$(ollama list 2>/dev/null | awk 'NR > 1 {print $1}')"; then
  echo "Could not list Ollama models. Ensure Ollama is running with: ollama serve" >&2
  exit 1
fi

model_exists() {
  local candidate="$1"
  printf '%s\n' "$MODEL_LIST" | awk -v model="$candidate" '$1 == model || $1 == model ":latest" { found = 1 } END { exit found ? 0 : 1 }'
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

echo "No HOCA-compatible Ollama model found after trying configured model, qwen-32b-pro, qwen-14b-pro, and qwen-7b-pro. Build one with scripts/install.sh or create qwen-32b-pro, qwen-14b-pro, or qwen-7b-pro." >&2
exit 1
