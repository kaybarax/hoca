#!/usr/bin/env bash
set -euo pipefail

# If a role model has already been resolved into LLM_MODEL, echo the bare model
# name for provider-backed runs.
if [[ -n "${LLM_MODEL:-}" ]]; then
  case "$LLM_MODEL" in
    openai/*|deepseek/*|gemini/*|anthropic/*|together_ai/*|openrouter/*)
      echo "${LLM_MODEL#*/}"
      exit 0
      ;;
    ollama/*)
      HOCA_REQUESTED_MODEL="${HOCA_REQUESTED_MODEL:-${LLM_MODEL#ollama/}}"
      ;;
  esac
fi

# Try LM Studio if configured or auto-detected
LMSTUDIO_URL="${LLM_BASE_URL:-http://localhost:1234/v1}"
if [[ "${HOCA_LLM_PROVIDER:-}" == "lmstudio" ]] || \
   { [[ -z "${HOCA_LLM_PROVIDER:-}" ]] && ! command -v ollama >/dev/null 2>&1 && \
     command -v curl >/dev/null 2>&1 && curl -fsS "$LMSTUDIO_URL/models" >/dev/null 2>&1; }; then
  if command -v curl >/dev/null 2>&1; then
    MODEL_JSON="$(curl -fsS "$LMSTUDIO_URL/models" 2>/dev/null || true)"
    if [[ -n "$MODEL_JSON" ]] && command -v jq >/dev/null 2>&1; then
      FIRST_MODEL="$(printf '%s' "$MODEL_JSON" | jq -r '.data[0].id // empty' 2>/dev/null || true)"
      if [[ -n "$FIRST_MODEL" ]]; then
        echo "$FIRST_MODEL"
        exit 0
      fi
    fi
  fi
  echo "LM Studio provider selected but no models found at $LMSTUDIO_URL" >&2
  exit 1
fi

# Ollama path (default)
if ! command -v ollama >/dev/null 2>&1; then
  echo "No LLM provider available. Install Ollama, start LM Studio, or configure a HOCA role model provider." >&2
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

if [[ -n "${HOCA_REQUESTED_MODEL:-}" ]]; then
  if model_exists "$HOCA_REQUESTED_MODEL"; then
    echo "$HOCA_REQUESTED_MODEL"
    exit 0
  fi
  echo "Requested HOCA model not found in Ollama: $HOCA_REQUESTED_MODEL" >&2
  echo "Build it with scripts/install.sh or create it with: ollama create $HOCA_REQUESTED_MODEL -f ./models/Modelfile.14b" >&2
  exit 1
fi

try_model "${OLLAMA_MODEL:-}"
try_model "qwen-14b-pro"
try_model "qwen-7b-pro"
try_model "qwen-32b-pro"

echo "No HOCA-compatible Ollama model found after trying configured model, qwen-14b-pro, qwen-7b-pro, and qwen-32b-pro. Build one with scripts/install.sh or create qwen-14b-pro, qwen-7b-pro, or qwen-32b-pro." >&2
exit 1
