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
PYTHON_BIN="${HOCA_PYTHON:-python3}"

run_review_gate() {
  local review_text_path="$1"
  set +e
  REVIEW_GATE_ARGS=(
    "$RUN_DIR"
    --review-text "$review_text_path"
    --run-id "$(basename "$RUN_DIR")"
    --round "${HOCA_REVIEW_ROUND:-1}"
    --project-path "$PROJECT_PATH"
  )
  if [ -n "${HOCA_REVIEW_REPORT_PATH:-}" ]; then
    REVIEW_GATE_ARGS+=(--structured-report "$HOCA_REVIEW_REPORT_PATH")
  fi
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m hoca.review_gate "${REVIEW_GATE_ARGS[@]}"
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

REVIEW_ROUND="${HOCA_REVIEW_ROUND:-1}"
RUN_ID="$(basename "$RUN_DIR")"
STRUCTURED_REPORT_PATH="$RUN_DIR/reviews/review-report-${REVIEW_ROUND}.json"
mkdir -p "$RUN_DIR/reviews"

CHANGED_FILES_FILE="$REVIEW_DIR/changed-files.txt"
FULL_DIFF_FILE="$REVIEW_DIR/git-diff.patch"
DIFF_FILE="$FULL_DIFF_FILE"
DIFF_VIEW_FILE="$DIFF_FILE"
TEST_SUMMARY_FILE="$RUN_DIR/tests-summary.md"
TEST_SUMMARY_VIEW_FILE="$TEST_SUMMARY_FILE"
TRUNCATION_FILE="$REVIEW_DIR/context-truncation.json"
REVIEW_DIFF_MAX_LINES="${HOCA_REVIEW_DIFF_MAX_LINES:-1200}"
REVIEW_DIFF_CONTEXT_LINES="${HOCA_REVIEW_DIFF_CONTEXT_LINES:-3}"
REVIEW_LOG_TAIL_LINES="${HOCA_REVIEW_LOG_TAIL_LINES:-200}"
printf '%s\n' "$CHANGED_FILES" > "$CHANGED_FILES_FILE"
git diff -U"$REVIEW_DIFF_CONTEXT_LINES" > "$FULL_DIFF_FILE"

DIFF_TRUNCATED=false
DIFF_LINE_COUNT="$(wc -l < "$FULL_DIFF_FILE" | tr -d ' ')"
if [ "$DIFF_LINE_COUNT" -gt "$REVIEW_DIFF_MAX_LINES" ]; then
  DIFF_VIEW_FILE="$REVIEW_DIR/git-diff.capped.patch"
  head -n "$REVIEW_DIFF_MAX_LINES" "$FULL_DIFF_FILE" > "$DIFF_VIEW_FILE"
  {
    echo ""
    echo "[HOCA truncated diff view at ${REVIEW_DIFF_MAX_LINES} of ${DIFF_LINE_COUNT} lines; full diff: ${FULL_DIFF_FILE}]"
  } >> "$DIFF_VIEW_FILE"
  DIFF_TRUNCATED=true
fi

TEST_SUMMARY_TRUNCATED=false
TEST_SUMMARY_LINE_COUNT=0
if [ -f "$TEST_SUMMARY_FILE" ]; then
  TEST_SUMMARY_LINE_COUNT="$(wc -l < "$TEST_SUMMARY_FILE" | tr -d ' ')"
  if [ "$TEST_SUMMARY_LINE_COUNT" -gt "$REVIEW_LOG_TAIL_LINES" ]; then
    TEST_SUMMARY_VIEW_FILE="$REVIEW_DIR/tests-summary-tail.md"
    tail -n "$REVIEW_LOG_TAIL_LINES" "$TEST_SUMMARY_FILE" > "$TEST_SUMMARY_VIEW_FILE"
    TEST_SUMMARY_TRUNCATED=true
  fi
fi

cat > "$TRUNCATION_FILE" <<EOF
{
  "diff_truncated": ${DIFF_TRUNCATED},
  "diff_full_path": "${FULL_DIFF_FILE}",
  "diff_view_path": "${DIFF_VIEW_FILE}",
  "diff_line_count": ${DIFF_LINE_COUNT},
  "diff_line_cap": ${REVIEW_DIFF_MAX_LINES},
  "test_summary_truncated": ${TEST_SUMMARY_TRUNCATED},
  "test_summary_full_path": "${TEST_SUMMARY_FILE}",
  "test_summary_view_path": "${TEST_SUMMARY_VIEW_FILE}",
  "test_summary_line_count": ${TEST_SUMMARY_LINE_COUNT},
  "test_summary_line_cap": ${REVIEW_LOG_TAIL_LINES}
}
EOF

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
- ${DIFF_VIEW_FILE}

Test summary view and context truncation metadata:
- ${TEST_SUMMARY_VIEW_FILE}
- ${TRUNCATION_FILE}

Inspect those files and the working tree directly. Do not rely on this prompt
as a complete copy of the diff. If context-truncation.json says a view was
capped, mention that limitation in pr_notes.summary."

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

Review-only constraints:
- Manager-owned Git lifecycle only: never run git add, git commit, git push, git merge, gh pr create, or gh pr merge.
- Do not modify, stage, commit, push, merge, or open pull requests.
- Do not implement fixes or edit repository files during this review pass.
- Inspect the changed files, diff, and working tree only to judge the submitted work.

Structural quality bar:
- Block correctness, security, scope, test, and material maintainability regressions.
- Prefer existing helpers and ownership boundaries over bespoke near-duplicates.
- Treat needless wrappers, one-off modes, scattered special cases, and avoidable
  file growth as risks only when they materially reduce future safety.
- Do not block on personal taste, naming preference, or formatting alone.

Produce a structured HocaReviewReport as JSON (YAML is acceptable only if JSON is
not practical). Write the report to:
- ${STRUCTURED_REPORT_PATH}

Include these fields:
- schema_version: 1
- run_id: ${RUN_ID}
- round: ${REVIEW_ROUND}
- role: reviewer
- verdict: LGTM | fix_required | blocked
- findings: list of classified findings (may be empty for LGTM)
- pr_notes.summary: decision-relevant context for the manager
- pr_notes.known_followups: deferred non-ship-blocking tech debt

Each finding must include:
- id (for example F1, F2)
- severity: critical | high | medium | low | nit
- category: correctness | security | test | scope | maintainability | style | tooling | environment
- file: repo-relative path or null
- summary: concise evidence-based description
- required_fix: non-null only when repair is required before approval; null for observations

Severity rubric:
- critical: severe correctness, security, or data-integrity defect; hard block
- high: material correctness or security defect; repair before LGTM
- medium: meaningful quality gap, often missing tests; usually repair
- low: real but often deferrable issue when the core change is sound
- nit: observation only; never a hard blocker

Distinguish blockers from PR tech debt:
- Blockers belong in findings with required_fix set and drive fix_required or blocked.
- PR tech debt belongs in pr_notes.known_followups with required_fix null on the finding.
- Do not block on pure preference, naming taste, or formatting when correctness,
  safety, tests, and scope are sound.
- Do not approve merely because behavior works: LGTM also requires no clear
  structural regression or unjustified scope growth.

Also include the structured JSON in your final response inside a fenced \`\`\`json block."

PROMPT_FILE="$REVIEW_DIR/openhands-review-prompt.txt"
printf '%s\n' "$REVIEW_TASK" > "$PROMPT_FILE"

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

if [ ! -f "$STRUCTURED_REPORT_PATH" ]; then
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m hoca.review_gate \
    "$RUN_DIR" \
    --materialize-from-text "$RUN_DIR/openhands-review.txt" \
    --run-id "$RUN_ID" \
    --round "$REVIEW_ROUND" \
    --output "$STRUCTURED_REPORT_PATH" \
    >/dev/null 2>&1 || true
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
