#!/usr/bin/env bash
# Resolve and export LLM_* variables for a HOCA agent role from the model pool.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: resolve-role-model-env.sh <manager|worker|reviewer|fallback>

Exports role-specific LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY from the resolved
role model.
EOF
}

if [ "$#" -lt 1 ]; then
  usage
  exit 1
fi

ROLE="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_BIN="${HOCA_PYTHON:-python3}"

EXPORTS="$(
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m hoca.role_model_env export "$ROLE" 2>/dev/null || true
)"

if [ -n "$EXPORTS" ]; then
  # shellcheck disable=SC1090
  eval "$EXPORTS"
  if [ -n "${HOCA_SELECTED_MODEL_SLOT:-}" ]; then
    echo "Resolved ${ROLE} model slot: ${HOCA_SELECTED_MODEL_SLOT} (provider model: ${LLM_MODEL})"
  fi
fi
