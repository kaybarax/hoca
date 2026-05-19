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
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

run_review_gate() {
  local review_text_path="$1"
  set +e
  REVIEW_GATE_ARGS=(
    "$RUN_DIR"
    --review-text "$review_text_path"
    --run-id "$(basename "$RUN_DIR")"
    --round "${HOCA_REVIEW_ROUND:-1}"
  )
  if [ -n "${HOCA_REVIEW_REPORT_PATH:-}" ]; then
    REVIEW_GATE_ARGS+=(--structured-report "$HOCA_REVIEW_REPORT_PATH")
  fi
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.review_gate "${REVIEW_GATE_ARGS[@]}"
  REVIEW_GATE_EXIT=$?
  set -e
}

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
  run_review_gate "$RUN_DIR/openhands-review.txt"
  exit "$REVIEW_GATE_EXIT"
fi

REVIEW_DIR="$RUN_DIR/review"
mkdir -p "$REVIEW_DIR"

CHANGED_FILES_FILE="$REVIEW_DIR/changed-files.txt"
DIFF_FILE="$REVIEW_DIR/git-diff.patch"
printf '%s\n' "$CHANGED_FILES" > "$CHANGED_FILES_FILE"
git diff > "$DIFF_FILE"

REVIEW_GOAL="$TASK"
ACCEPTANCE_BLOCK=""
NON_GOALS_BLOCK=""
EXPECTED_AREAS_BLOCK=""
TASK_SPEC_FILE="$RUN_DIR/task-spec.json"
if [ -f "$TASK_SPEC_FILE" ] && command -v jq >/dev/null 2>&1; then
  spec_goal="$(jq -r '.goal // empty' "$TASK_SPEC_FILE")"
  if [ -n "$spec_goal" ]; then
    REVIEW_GOAL="$spec_goal"
  fi
  acceptance_lines="$(jq -r '.acceptance_criteria[]?' "$TASK_SPEC_FILE" 2>/dev/null || true)"
  if [ -n "$acceptance_lines" ]; then
    ACCEPTANCE_BLOCK="$(printf '%s\n' "$acceptance_lines" | sed 's/^/- /')"
  fi
  non_goal_lines="$(jq -r '.non_goals[]?' "$TASK_SPEC_FILE" 2>/dev/null || true)"
  if [ -n "$non_goal_lines" ]; then
    NON_GOALS_BLOCK="$(printf '%s\n' "$non_goal_lines" | sed 's/^/- /')"
  fi
  expected_area_lines="$(jq -r '.expected_areas[]?' "$TASK_SPEC_FILE" 2>/dev/null || true)"
  if [ -n "$expected_area_lines" ]; then
    EXPECTED_AREAS_BLOCK="$(printf '%s\n' "$expected_area_lines" | sed 's/^/- /')"
  fi
fi

REVIEW_TASK="Review the current repository changes for the following task: ${REVIEW_GOAL}

The changed-file list and diff are saved in:
- ${CHANGED_FILES_FILE}
- ${DIFF_FILE}

Inspect those files and the working tree directly. Do not rely on this prompt
as a complete copy of the diff."

if [ -n "$ACCEPTANCE_BLOCK" ]; then
  REVIEW_TASK="${REVIEW_TASK}

Acceptance criteria:
${ACCEPTANCE_BLOCK}"
fi

if [ -n "$EXPECTED_AREAS_BLOCK" ]; then
  REVIEW_TASK="${REVIEW_TASK}

Expected areas:
${EXPECTED_AREAS_BLOCK}"
fi

if [ -n "$NON_GOALS_BLOCK" ]; then
  REVIEW_TASK="${REVIEW_TASK}

Non-goals:
${NON_GOALS_BLOCK}"
fi

REVIEW_TASK="${REVIEW_TASK}

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

echo "Running OpenHands review..."
set +e
HOCA_AGENT_ROLE=reviewer "$SCRIPT_DIR/run-openhands-task.sh" "$PROJECT_PATH" "$REVIEW_TASK" "$REVIEW_DIR"
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

run_review_gate "$RUN_DIR/openhands-review.txt"

if [ "$REVIEW_GATE_EXIT" -eq 0 ]; then
  echo "OpenHands review passed."
elif [ "$REVIEW_GATE_EXIT" -eq 2 ]; then
  echo "OpenHands review did not return LGTM."
  exit 2
elif [ "$REVIEW_GATE_EXIT" -eq 4 ]; then
  echo "OpenHands review was blocked."
  exit 4
else
  echo "OpenHands review gate failed."
  exit "$REVIEW_GATE_EXIT"
fi
