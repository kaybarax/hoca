#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: run-hoca-task.sh /path/to/project \"task\" [--issue-id ID] [--auto-merge] [--notify-telegram]"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
shift 2

ISSUE_ID=""
AUTO_MERGE="false"
NOTIFY_TELEGRAM="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --issue-id)
      ISSUE_ID="$2"
      shift 2
      ;;
    --auto-merge)
      AUTO_MERGE="true"
      shift
      ;;
    --notify-telegram)
      NOTIFY_TELEGRAM="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH"
  exit 1
fi

mkdir -p .hoca-runtime/runs .hoca-runtime/logs

if [ -n "$ISSUE_ID" ]; then
  RUN_ID="issue-${ISSUE_ID}"
  LOCK_FILE=".hoca-runtime/runs/issue-${ISSUE_ID}.lock"
else
  RUN_ID="run-$(date -u +%Y%m%dT%H%M%SZ)"
  LOCK_FILE=".hoca-runtime/runs/${RUN_ID}.lock"
fi

RUN_DIR=".hoca-runtime/runs/${RUN_ID}"
mkdir -p "$RUN_DIR"

if [ -f "$LOCK_FILE" ]; then
  echo "Another HOCA run appears to be active for this task: $LOCK_FILE"
  exit 0
fi

cat > "$LOCK_FILE" <<EOF
{
  "run_id": "$RUN_ID",
  "issue_id": "$ISSUE_ID",
  "task": $(printf '%s' "$TASK" | jq -Rs .),
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

cleanup() {
  rm -f "$LOCK_FILE"
}
trap cleanup EXIT

update_status() {
  local new_status="$1"
  local reason="${2:-}"
  if command -v jq >/dev/null 2>&1 && [ -f "$RUN_DIR/status.json" ]; then
    if [ -n "$reason" ]; then
      jq --arg s "$new_status" --arg r "$reason" '.status = $s | .reason = $r' "$RUN_DIR/status.json" > "$RUN_DIR/status.tmp"
    else
      jq --arg s "$new_status" '.status = $s' "$RUN_DIR/status.json" > "$RUN_DIR/status.tmp"
    fi
    mv "$RUN_DIR/status.tmp" "$RUN_DIR/status.json"
  fi
}

cat > "$RUN_DIR/status.json" <<EOF
{
  "run_id": "$RUN_ID",
  "status": "started",
  "task": $(printf '%s' "$TASK" | jq -Rs .),
  "issue_id": "$ISSUE_ID",
  "auto_merge": "$AUTO_MERGE",
  "notify_telegram": "$NOTIFY_TELEGRAM",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "HOCA run started: $RUN_ID"

"$SCRIPT_DIR/init-project.sh" "$PROJECT_PATH" 2>/dev/null || true

echo "Checking working tree..."
if [ -n "$(git status --short)" ]; then
  echo "Working tree has existing changes:"
  git status --short
  echo "Stopping to avoid mixing human and agent changes."
  update_status "blocked" "dirty_working_tree"
  exit 1
fi

if [ -n "$ISSUE_ID" ]; then
  BRANCH="fix/issue-${ISSUE_ID}"
else
  SLUG="$(printf '%s' "$TASK" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//' | sed 's/-$//' | cut -c1-50)"
  BRANCH="feat/${SLUG:-hoca-task}"
fi

echo "Creating branch: $BRANCH"
git checkout -b "$BRANCH"

echo "Running OpenHands..."
"$SCRIPT_DIR/run-openhands-task.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR"

echo "Running tests..."
"$SCRIPT_DIR/run-tests.sh" "$PROJECT_PATH" "$RUN_DIR"

echo "Running Aider review..."
"$SCRIPT_DIR/review-with-aider.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR"

if ! grep -q "LGTM" "$RUN_DIR/aider-review.txt"; then
  echo "Aider did not return LGTM. Stopping before commit."
  update_status "blocked" "aider_not_lgtm"
  exit 1
fi

echo "Inspecting changed files..."
git status --short | tee "$RUN_DIR/git-status.txt"
git diff > "$RUN_DIR/git-diff.patch"
git status --short | awk '{print $NF}' > "$RUN_DIR/changed-files.txt"

if [ -z "$(git status --short)" ]; then
  echo "No changes produced."
  update_status "no_changes"
  exit 0
fi

INTENDED_FILE_LIST="$RUN_DIR/intended-files.txt"
if [ ! -f "$INTENDED_FILE_LIST" ]; then
  echo "Automatic safe staging requires Manager or Reviewer to write: $INTENDED_FILE_LIST"
  echo "Changed files are recorded in $RUN_DIR/changed-files.txt"
  update_status "needs_human_staging" "intended_file_list_required"

  if [ "$NOTIFY_TELEGRAM" = "true" ]; then
    "$SCRIPT_DIR/notify.sh" "$PROJECT_PATH" "$RUN_DIR" 2>/dev/null || true
  fi

  echo "HOCA run completed up to review. Human staging required."
  exit 0
fi

echo "Running automatic safe staging from reviewed intended file list..."
"$SCRIPT_DIR/safe-stage-after-review.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "$INTENDED_FILE_LIST"
git diff --cached > "$RUN_DIR/staged-diff.patch"
update_status "staged" "safe_staging_complete"

RUN_DIR_ABS="$(cd "$RUN_DIR" && pwd)"
COMMIT_EXTRA=()
if [ -n "$ISSUE_ID" ]; then
  COMMIT_EXTRA=(--issue-id "$ISSUE_ID")
fi
if ! "$SCRIPT_DIR/commit-after-staging.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR_ABS" "${COMMIT_EXTRA[@]}"; then
  update_status "blocked" "commit_failed"
  exit 1
fi

update_status "committed" "commit_complete"

if [ "$NOTIFY_TELEGRAM" = "true" ]; then
  "$SCRIPT_DIR/notify.sh" "$PROJECT_PATH" "$RUN_DIR" 2>/dev/null || true
fi

echo "HOCA run completed through commit. Hash recorded in $RUN_DIR/commit-hash.txt"
