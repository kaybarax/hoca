#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: review-with-openhands.sh /path/to/project \"task\" /path/to/run-dir"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$(mkdir -p "$3" && cd "$3" && pwd)"

cd "$PROJECT_PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

changed_files_for_review() {
  {
    git diff --name-only --diff-filter=ACMRTUXB
    git ls-files --others --exclude-standard
  } | while IFS= read -r changed_path || [ -n "$changed_path" ]; do
    [ -n "$changed_path" ] || continue
    case "$changed_path" in
      .hoca-runtime|.hoca-runtime/*) continue ;;
    esac
    [ -e "$changed_path" ] || continue
    printf '%s\n' "$changed_path"
  done | sort -u
}

CHANGED_FILES="$(changed_files_for_review)"
if [ -z "$CHANGED_FILES" ]; then
  echo "No changed files to review."
  echo "LGTM" > "$RUN_DIR/openhands-review.txt"
  exit 0
fi

DIFF_OUTPUT="$(git diff)"

REVIEW_TASK="Review the current repository changes for the following task: ${TASK}

Here are the changed files:
${CHANGED_FILES}

Here is the diff of changes:
\`\`\`
${DIFF_OUTPUT}
\`\`\`

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

REVIEW_DIR="$RUN_DIR/review"
mkdir -p "$REVIEW_DIR"

echo "Running OpenHands review..."
set +e
"$SCRIPT_DIR/run-openhands-task.sh" "$PROJECT_PATH" "$REVIEW_TASK" "$REVIEW_DIR"
REVIEW_EXIT=$?
set -e

if [ -f "$REVIEW_DIR/openhands-output.log" ]; then
  cp "$REVIEW_DIR/openhands-output.log" "$RUN_DIR/openhands-review.txt"
elif [ -f "$REVIEW_DIR/openhands-output.jsonl" ]; then
  cp "$REVIEW_DIR/openhands-output.jsonl" "$RUN_DIR/openhands-review.txt"
else
  echo "OpenHands review produced no output." > "$RUN_DIR/openhands-review.txt"
fi

if [ -f "$REVIEW_DIR/openhands-stderr.log" ]; then
  cp "$REVIEW_DIR/openhands-stderr.log" "$RUN_DIR/openhands-review-stderr.log"
fi

if [ -f "$REVIEW_DIR/openhands-exit-code.txt" ]; then
  cp "$REVIEW_DIR/openhands-exit-code.txt" "$RUN_DIR/openhands-review-exit-code.txt"
fi

if [ "$REVIEW_EXIT" -ne 0 ]; then
  echo "OpenHands review failed with exit code $REVIEW_EXIT."
  exit "$REVIEW_EXIT"
fi

if grep -q "LGTM" "$RUN_DIR/openhands-review.txt"; then
  echo "OpenHands review passed."
else
  echo "OpenHands review did not return LGTM."
  exit 2
fi
