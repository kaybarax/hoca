#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: review-with-aider.sh /path/to/project \"task\" /path/to/run-dir"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$(mkdir -p "$3" && cd "$3" && pwd)"

cd "$PROJECT_PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELECTED_MODEL="$("$SCRIPT_DIR/select-model.sh")"
AIDER_MODEL="${AIDER_MODEL:-ollama_chat/$SELECTED_MODEL}"

if ! command -v aider >/dev/null 2>&1; then
  echo "aider command not found." | tee "$RUN_DIR/aider-review.txt"
  exit 1
fi

PROMPT="Review the current repository changes for task: ${TASK}
Check:
- Whether the task was fulfilled.
- Whether the implementation is minimal and avoids unnecessary changes.
- Whether unrelated files were changed.
- Whether tests are sufficient.
- Whether security risks were introduced.
- Whether secrets or credentials were exposed.
- Whether generated files should be excluded from the commit.
- Whether the change is safe to commit.
If the changes are acceptable, end your response with exactly: LGTM
If not acceptable, list required fixes clearly."

echo "Running Aider review with model: $AIDER_MODEL"

AIDER_REVIEW_ARGS=(
  --model "$AIDER_MODEL"
  --no-gitignore
  --no-show-model-warnings
  --no-show-release-notes
  --map-tokens 0
  --input-history-file "$RUN_DIR/aider-input.history"
  --chat-history-file "$RUN_DIR/aider-chat-history.md"
  --llm-history-file "$RUN_DIR/aider-llm-history.md"
  --message "$PROMPT"
)

if aider --help 2>&1 | grep -q -- "--read-only"; then
  AIDER_REVIEW_ARGS+=(--read-only)
else
  AIDER_REVIEW_ARGS+=(--dry-run --no-auto-commits --no-dirty-commits)
fi

set +e
aider "${AIDER_REVIEW_ARGS[@]}" \
  > "$RUN_DIR/aider-review.txt" 2> "$RUN_DIR/aider-stderr.log"
EXIT_CODE=$?
set -e

echo "$EXIT_CODE" > "$RUN_DIR/aider-exit-code.txt"

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "Aider failed with exit code $EXIT_CODE."
  exit "$EXIT_CODE"
fi

if grep -q "LGTM" "$RUN_DIR/aider-review.txt"; then
  echo "Aider review passed."
else
  echo "Aider review did not return LGTM."
  exit 2
fi
