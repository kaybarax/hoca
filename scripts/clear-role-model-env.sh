#!/usr/bin/env bash
# Remove role-specific and pooled LLM credentials from the environment.
set -euo pipefail

unset LLM_MODEL LLM_BASE_URL LLM_API_KEY HOCA_SELECTED_MODEL_SLOT HOCA_REQUESTED_MODEL OLLAMA_MODEL || true

for index in 1 2 3 4 5; do
  unset "HOCA_MODEL_${index}_API_KEY" || true
done
