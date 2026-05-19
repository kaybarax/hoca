#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: generate-task-report.sh /path/to/project /path/to/run-dir" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
RUN_DIR="$(mkdir -p "$2" && cd "$2" && pwd)"
REPORT_FILE="$RUN_DIR/task-report.md"
STATUS_FILE="$RUN_DIR/status.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_PATH"

json_value() {
  local key="$1"
  local fallback="${2:-}"
  if command -v jq >/dev/null 2>&1 && [ -f "$STATUS_FILE" ]; then
    jq -r --arg k "$key" --arg fallback "$fallback" '.[$k] // $fallback' "$STATUS_FILE"
  else
    printf '%s\n' "$fallback"
  fi
}

markdown_value() {
  local value="$1"
  if [ -n "$value" ] && [ "$value" != "null" ]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "None"
  fi
}

append_file_list() {
  local file="$1"
  if [ -s "$file" ]; then
    sed '/^[[:space:]]*$/d' "$file" | sort -u | sed 's/^/- /'
  else
    echo "- None recorded"
  fi
}

append_structured_artifact() {
  local label="$1"
  local file="$2"
  if [ -f "$file" ] && command -v jq >/dev/null 2>&1; then
    echo "### $label"
    jq -r '
      if type == "object" then
        to_entries
        | map("- \(.key): \(if (.value|type) == "array" then (.value|join(", ")) else (.value|tostring) end)")
        | .[]
      else
        "- (unstructured)"
      end
    ' "$file" 2>/dev/null | sed -n '1,24p'
    echo ""
  fi
}

append_log_links() {
  local found=0
  for file in \
    "$RUN_DIR/raw-task.txt" \
    "$RUN_DIR/task-spec.json" \
    "$RUN_DIR/sandbox-policy.json" \
    "$RUN_DIR/final-state.json" \
    "$RUN_DIR/openhands-output.log" \
    "$RUN_DIR/openhands-stderr.log" \
    "$RUN_DIR/tests-output.log" \
    "$RUN_DIR/tests-stderr.log" \
    "$RUN_DIR/openhands-review.txt" \
    "$RUN_DIR/openhands-review-stderr.log" \
    "$RUN_DIR/git-status.txt" \
    "$RUN_DIR/git-diff.patch" \
    "$RUN_DIR/staged-diff.patch" \
    "$RUN_DIR/gh-pr-create.log" \
    "$RUN_DIR/gh-pr-merge.log" \
    "$RUN_DIR/research-sources.txt" \
    "$RUN_DIR/merge-policy.txt" \
    "$RUN_DIR"/attempts/worker-attempt-*.json \
    "$RUN_DIR"/reviews/review-report-*.json \
    "$RUN_DIR"/decisions/manager-decision-*.json \
    "$RUN_DIR"/validation/validation-report-*.json; do
    if [ -f "$file" ]; then
      printf -- "- %s\n" "$file"
      found=1
    fi
  done
  if [ "$found" -eq 0 ]; then
    echo "- None recorded"
  fi
}

RUN_ID="$(json_value run_id "$(basename "$RUN_DIR")")"
TASK="$(json_value task "")"
ISSUE_ID="$(json_value issue_id "")"
STATUS="$(json_value status "unknown")"
REASON="$(json_value reason "")"
STARTED_AT="$(json_value started_at "")"
AUTO_MERGE="$(json_value auto_merge "false")"
MERGE_PERFORMED="$(json_value merge_performed "false")"
AUTO_MERGE_QUEUED="$(json_value auto_merge_queued "false")"
ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

BRANCH="$(git branch --show-current 2>/dev/null || true)"
PR_URL=""
if [ -f "$RUN_DIR/pr-url.txt" ]; then
  PR_URL="$(head -n 1 "$RUN_DIR/pr-url.txt")"
fi

COMMIT_HASH=""
if [ -f "$RUN_DIR/commit-hash.txt" ]; then
  COMMIT_HASH="$(head -n 1 "$RUN_DIR/commit-hash.txt")"
fi

FAILED_COMMAND=""
if [ -f "$RUN_DIR/failed-command.txt" ]; then
  FAILED_COMMAND="$(head -n 1 "$RUN_DIR/failed-command.txt")"
fi

if [ "$MERGE_PERFORMED" = "true" ]; then
  MERGE_STATUS="merged"
elif [ "$AUTO_MERGE_QUEUED" = "true" ]; then
  MERGE_STATUS="auto-merge enabled"
elif [ "$AUTO_MERGE" = "true" ]; then
  MERGE_STATUS="auto-merge requested"
else
  MERGE_STATUS="not merged"
fi

CODE_REVIEW="Not run"
REVIEW_ROUND="${HOCA_REVIEW_ROUND:-1}"
STRUCTURED_REVIEW="$RUN_DIR/reviews/review-report-${REVIEW_ROUND}.json"
if [ -f "$RUN_DIR/openhands-review.txt" ] || [ -f "$STRUCTURED_REVIEW" ]; then
  CODE_REVIEW="$(
    PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.review_gate "$RUN_DIR" \
      --review-text "$RUN_DIR/openhands-review.txt" \
      --run-id "$(basename "$RUN_DIR")" \
      --round "$REVIEW_ROUND" \
      --print status 2>/dev/null || true
  )"
  if [ -z "$CODE_REVIEW" ]; then
    CODE_REVIEW="review gate error"
  fi
fi

{
  echo "## HOCA Task Report"
  echo ""
  echo "### Task"
  if [ -f "$RUN_DIR/raw-task.txt" ]; then
    sed -n '1,80p' "$RUN_DIR/raw-task.txt"
  else
    markdown_value "$TASK"
  fi
  if [ -f "$RUN_DIR/task-spec.json" ] && command -v jq >/dev/null 2>&1; then
    spec_goal="$(jq -r '.goal // empty' "$RUN_DIR/task-spec.json")"
    if [ -n "$spec_goal" ] && [ "$spec_goal" != "$(head -n 1 "$RUN_DIR/raw-task.txt" 2>/dev/null || true)" ]; then
      echo ""
      echo "Refined goal:"
      markdown_value "$spec_goal"
    fi
  fi
  echo ""
  echo "### Run"
  echo "- Run ID: $(markdown_value "$RUN_ID")"
  echo "- Issue ID: $(markdown_value "$ISSUE_ID")"
  echo "- Start time: $(markdown_value "$STARTED_AT")"
  echo "- End time: $ENDED_AT"
  echo "- Final status: $(markdown_value "$STATUS")"
  if [ -n "$REASON" ] && [ "$REASON" != "null" ]; then
    echo "- Blocked reason: $REASON"
  fi
  if [ -n "$FAILED_COMMAND" ]; then
    echo "- Failed command: \`$FAILED_COMMAND\`"
  fi
  echo ""
  echo "### Branch"
  markdown_value "$BRANCH"
  echo ""
  echo "### Pull Request"
  markdown_value "$PR_URL"
  echo ""
  echo "### Files Changed"
  append_file_list "$RUN_DIR/changed-files.txt"
  if [ -s "$RUN_DIR/staged-files.txt" ]; then
    echo ""
    echo "Staged files:"
    append_file_list "$RUN_DIR/staged-files.txt"
  fi
  echo ""
  echo "### Summary"
  if [ -n "$COMMIT_HASH" ]; then
    echo "- Commit created: \`$COMMIT_HASH\`"
  fi
  case "$STATUS" in
    committed)
      echo "- Task completed through commit creation."
      ;;
    pr_created)
      echo "- Task completed through pull request creation."
      ;;
    staged)
      echo "- Task completed through safe staging and is ready for commit."
      ;;
    no_changes)
      echo "- Task produced no repository changes."
      ;;
    needs_human_staging)
      echo "- Task completed through review and requires human staging."
      ;;
    blocked)
      echo "- Task stopped before completion."
      ;;
    failed)
      echo "- Task failed before completion."
      ;;
    *)
      echo "- Run status recorded as: $STATUS"
      ;;
  esac
  echo ""
  echo "### Structured Artifacts"
  append_structured_artifact "Task Spec" "$RUN_DIR/task-spec.json"
  append_structured_artifact "Sandbox Policy" "$RUN_DIR/sandbox-policy.json"
  latest_validation="$(ls -1 "$RUN_DIR"/validation/validation-report-*.json 2>/dev/null | sort -V | tail -n 1 || true)"
  if [ -n "$latest_validation" ]; then
    append_structured_artifact "Validation Report" "$latest_validation"
  fi
  latest_review="$(ls -1 "$RUN_DIR"/reviews/review-report-*.json 2>/dev/null | sort -V | tail -n 1 || true)"
  if [ -n "$latest_review" ]; then
    append_structured_artifact "Review Report" "$latest_review"
  fi
  latest_decision="$(ls -1 "$RUN_DIR"/decisions/manager-decision-*.json 2>/dev/null | sort -V | tail -n 1 || true)"
  if [ -n "$latest_decision" ]; then
    append_structured_artifact "Manager Decision" "$latest_decision"
  fi
  append_structured_artifact "Final State" "$RUN_DIR/final-state.json"
  echo ""
  echo "### Validation"
  if [ -f "$RUN_DIR/tests-summary.md" ]; then
    sed -n '1,80p' "$RUN_DIR/tests-summary.md"
  elif [ -f "$RUN_DIR/tests-exit-code.txt" ]; then
    echo "- Test exit code: $(head -n 1 "$RUN_DIR/tests-exit-code.txt")"
  else
    echo "- No validation summary recorded."
  fi
  echo ""
  echo "### Code Review"
  echo "- Status: $CODE_REVIEW"
  if [ -f "$RUN_DIR/openhands-review-exit-code.txt" ]; then
    echo "- Exit code: $(head -n 1 "$RUN_DIR/openhands-review-exit-code.txt")"
  fi
  echo ""
  echo "### Merge Status"
  echo "- $MERGE_STATUS"
  if [ -f "$RUN_DIR/merge-policy.txt" ]; then
    echo "- Merge policy recorded at: $RUN_DIR/merge-policy.txt"
  fi
  echo ""
  echo "### Research Sources"
  if [ -s "$RUN_DIR/research-sources.txt" ]; then
    sed -n '1,40p' "$RUN_DIR/research-sources.txt" | sed 's/^/- /'
  else
    echo "- No external research sources used."
  fi
  echo ""
  echo "### Notes"
  if [ -f "$RUN_DIR/risk-notes.txt" ]; then
    sed -n '1,80p' "$RUN_DIR/risk-notes.txt" | sed 's/^/- /'
  else
    echo "- No risk notes recorded."
  fi
  echo "- Local run artifacts:"
  append_log_links
} > "$REPORT_FILE"

echo "Task report written to $REPORT_FILE"
