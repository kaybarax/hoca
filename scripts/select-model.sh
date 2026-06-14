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

if [[ -n "${HOCA_REQUESTED_MODEL:-}" ]]; then
  if [[ -n "${OLLAMA_MODEL:-}" && "$OLLAMA_MODEL" != "$HOCA_REQUESTED_MODEL" ]]; then
    echo "Requested HOCA model differs from configured OLLAMA_MODEL: $HOCA_REQUESTED_MODEL vs $OLLAMA_MODEL" >&2
    exit 1
  fi
  if command -v ollama >/dev/null 2>&1; then
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
    if printf '%s\n' "$MODEL_LIST" | awk -v model="$HOCA_REQUESTED_MODEL" '$1 == model || $1 == model ":latest" { found = 1 } END { exit found ? 0 : 1 }'; then
      echo "$HOCA_REQUESTED_MODEL"
      exit 0
    fi
    echo "Requested HOCA model not found in Ollama: $HOCA_REQUESTED_MODEL" >&2
    echo "Configure the exact model in the env file or create it before running HOCA." >&2
    exit 1
  fi
  echo "$HOCA_REQUESTED_MODEL"
  exit 0
fi

if [[ -n "${OLLAMA_MODEL:-}" ]]; then
  if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama is required to validate OLLAMA_MODEL, but the command is missing." >&2
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
  if printf '%s\n' "$MODEL_LIST" | awk -v model="$OLLAMA_MODEL" '$1 == model || $1 == model ":latest" { found = 1 } END { exit found ? 0 : 1 }'; then
    echo "$OLLAMA_MODEL"
    exit 0
  fi
  echo "Configured OLLAMA_MODEL not found in Ollama: $OLLAMA_MODEL" >&2
  echo "Configure the exact model in the env file or create it before running HOCA." >&2
  exit 1
fi

echo "No model configured. Set HOCA role model blocks in the env file; HOCA will not auto-select a local model." >&2
exit 1
