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

echo "Validating target repository..."
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH"
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
CURRENT_BRANCH="$(git branch --show-current)"
PRE_RUN_STATUS="$(git status --short)"

echo "Repository root: $REPO_ROOT"
if [ -n "$CURRENT_BRANCH" ]; then
  echo "Current branch: $CURRENT_BRANCH"
else
  echo "Current branch: (detached HEAD)"
  echo "Stopping because HOCA requires a named branch before task execution."
  exit 1
fi

echo "Working tree status before run:"
if [ -n "$PRE_RUN_STATUS" ]; then
  printf '%s\n' "$PRE_RUN_STATUS"
  echo "Stopping to avoid mixing unrelated human changes with agent changes."
  exit 1
else
  echo "  clean"
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

LOCK_OWNER="${RUN_ID}-$$-$(date -u +%Y%m%dT%H%M%SZ)"
LOCK_METADATA_FILE="$RUN_DIR/lock-metadata.json"
cat > "$LOCK_METADATA_FILE" <<EOF
{
  "run_id": "$RUN_ID",
  "issue_id": "$ISSUE_ID",
  "owner_token": "$LOCK_OWNER",
  "pid": $$,
  "task": $(printf '%s' "$TASK" | jq -Rs .),
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

if ! (set -o noclobber; cat "$LOCK_METADATA_FILE" > "$LOCK_FILE") 2>/dev/null; then
  echo "Another HOCA run appears to be active for this task: $LOCK_FILE"
  exit 0
fi

cleanup() {
  if [ -d "$RUN_DIR" ] && [ -f "$RUN_DIR/status.json" ]; then
    "$SCRIPT_DIR/generate-task-report.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null 2>&1 || true
  fi
  if [ -f "$LOCK_FILE" ] && grep -q "\"owner_token\": \"$LOCK_OWNER\"" "$LOCK_FILE"; then
    rm -f "$LOCK_FILE"
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 129' HUP
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

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

write_failure_reason() {
  local reason="$1"
  local detail="${2:-}"
  printf '%s\n' "$reason" > "$RUN_DIR/failure-reason.txt"
  if [ -n "$detail" ]; then
    printf '%s\n' "$detail" > "$RUN_DIR/failure-detail.txt"
  fi
}

fail_run() {
  local reason="$1"
  local message="$2"
  echo "$message" >&2
  write_failure_reason "$reason" "$message"
  update_status "failed" "$reason"
  exit 1
}

block_run() {
  local reason="$1"
  local message="$2"
  echo "$message" >&2
  write_failure_reason "$reason" "$message"
  update_status "blocked" "$reason"
  exit 1
}

record_failed_command() {
  local exit_code="$?"
  local command="$BASH_COMMAND"
  if [[ "$command" == exit* ]]; then
    return
  fi
  if [ "$exit_code" -ne 0 ] && [ -d "$RUN_DIR" ]; then
    printf '%s\n' "$command" > "$RUN_DIR/failed-command.txt"
    update_status "failed" "command_failed"
  fi
}
trap record_failed_command ERR

cat > "$RUN_DIR/status.json" <<EOF
{
  "run_id": "$RUN_ID",
  "status": "started",
  "task": $(printf '%s' "$TASK" | jq -Rs .),
  "issue_id": "$ISSUE_ID",
  "auto_merge": "$AUTO_MERGE",
  "notify_telegram": "$NOTIFY_TELEGRAM",
  "repo_root": $(printf '%s' "$REPO_ROOT" | jq -Rs .),
  "starting_branch": $(printf '%s' "$CURRENT_BRANCH" | jq -Rs .),
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "HOCA run started: $RUN_ID"

{
  echo "Repository root: $REPO_ROOT"
  echo "Current branch: $CURRENT_BRANCH"
  echo "Working tree status before run:"
  echo "clean"
} > "$RUN_DIR/workspace-validation.txt"

echo "Running HOCA doctor preflight..."
if ! "$SCRIPT_DIR/hoca-doctor.sh" > "$RUN_DIR/doctor-output.log" 2> "$RUN_DIR/doctor-stderr.log"; then
  cat "$RUN_DIR/doctor-output.log"
  cat "$RUN_DIR/doctor-stderr.log" >&2
  fail_run "doctor_failed" "HOCA doctor failed. Stop and follow the install guidance above before running this task again."
fi

if [ "${HOCA_RUN_INIT_PROJECT:-false}" = "true" ]; then
  "$SCRIPT_DIR/init-project.sh" "$PROJECT_PATH" 2>/dev/null || true
fi

echo "Checking working tree..."
if [ -n "$(git status --short)" ]; then
  echo "Working tree has existing changes:"
  git status --short
  block_run "dirty_working_tree" "Stopping to avoid mixing human and agent changes."
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
set +e
"$SCRIPT_DIR/run-openhands-task.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR"
OPENHANDS_EXIT=$?
set -e
if [ "$OPENHANDS_EXIT" -ne 0 ]; then
  if [ -f "$RUN_DIR/monitor-result.json" ] && command -v jq >/dev/null 2>&1; then
    STOP_REASON="$(jq -r '.stop_reason // "unknown"' "$RUN_DIR/monitor-result.json")"
    if [ "$STOP_REASON" != "completed" ]; then
      block_run "openhands_${STOP_REASON}" "OpenHands was stopped by the safety monitor ($STOP_REASON). Logs were saved in $RUN_DIR."
    fi
  fi
  fail_run "openhands_failed" "OpenHands failed with exit code $OPENHANDS_EXIT. Logs were saved in $RUN_DIR."
fi

path_is_secret_like() {
  local path="$1"
  local lower
  lower="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"
  local base
  base="$(basename "$lower")"
  case "$base" in
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa|id_rsa.*|id_ed25519|id_ed25519.*|*.kubeconfig|*.keystore|*.jks|*credentials*|*.secret|*.secrets|.netrc|.npmrc|.pypirc|.htpasswd)
      return 0
      ;;
  esac
  case "$lower" in
    .github/secrets|.github/secrets/*)
      return 0
      ;;
  esac
  return 1
}

git status --short | awk '{print $NF}' > "$RUN_DIR/changed-files-after-openhands.txt"
while IFS= read -r changed_path || [ -n "$changed_path" ]; do
  [ -z "$changed_path" ] && continue
  if path_is_secret_like "$changed_path"; then
    printf '%s\n' "$changed_path" > "$RUN_DIR/secret-detected.txt"
    fail_run "secret_detected" "Secret-like changed file detected after OpenHands: $changed_path. Stopping immediately."
  fi
done < "$RUN_DIR/changed-files-after-openhands.txt"

echo "Running tests..."
set +e
"$SCRIPT_DIR/run-tests.sh" "$PROJECT_PATH" "$RUN_DIR"
TESTS_EXIT=$?
set -e
if [ "$TESTS_EXIT" -ne 0 ]; then
  fail_run "tests_failed" "Tests failed. Stopping before review or commit; see $RUN_DIR/tests-summary.md."
fi

echo "Running Aider review..."
set +e
"$SCRIPT_DIR/review-with-aider.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR"
AIDER_EXIT=$?
set -e
if [ "$AIDER_EXIT" -eq 2 ]; then
  block_run "aider_not_lgtm" "Aider did not return LGTM. Stopping and recording required fixes in $RUN_DIR/aider-review.txt."
elif [ "$AIDER_EXIT" -ne 0 ]; then
  fail_run "aider_failed" "Aider failed with exit code $AIDER_EXIT. Stopping before commit; see $RUN_DIR/aider-review.txt and aider-stderr.log."
fi

if ! grep -q "LGTM" "$RUN_DIR/aider-review.txt"; then
  block_run "aider_not_lgtm" "Aider did not return LGTM. Stopping before commit."
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

MERGE_POLICY_FILE="$RUN_DIR/merge-policy.txt"
{
  echo "HOCA merge policy (18.1 default no-merge; 18.2 optional guarded auto-merge)"
  echo ""
  echo "- This run does not invoke gh pr merge. Pull requests stay open for human review by default."
  echo "- When you run create-pr.sh, HOCA may queue GitHub auto-merge only if status.json has auto_merge true and scripts/auto-merge-guards.sh prechecks all pass (see README)."
  echo "- Remote branches are not deleted from this step; GitHub deletes the branch after merge when auto-merge uses --delete-branch and the merge completes."
  echo ""
  echo "Next step: open a pull request (when tests and review are complete):"
  echo "  $SCRIPT_DIR/create-pr.sh \"$PROJECT_PATH\" <task-one-line> \"$RUN_DIR_ABS\""
  if [ -n "$ISSUE_ID" ]; then
    echo "  (add --issue-id \"$ISSUE_ID\" if the PR should reference the issue)"
  fi
  if [ "$AUTO_MERGE" = "true" ]; then
    echo ""
    echo "This run requested --auto-merge: add risk-level.txt (first line: low) to the run directory before create-pr if you want guarded auto-merge, and ensure the GitHub repo enables \"Allow auto-merge\"."
  fi
} > "$MERGE_POLICY_FILE"

if command -v jq >/dev/null 2>&1 && [ -f "$RUN_DIR/status.json" ]; then
  jq \
    --arg mp "no_auto_merge_default" \
    --arg bd "only_after_successful_merge" \
    '.merge_policy = $mp | .merge_performed = false | .branch_delete_policy = $bd' \
    "$RUN_DIR/status.json" > "$RUN_DIR/status.tmp"
  mv "$RUN_DIR/status.tmp" "$RUN_DIR/status.json"
fi

"$SCRIPT_DIR/generate-task-report.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null

echo ""
echo "------------------------------------------------------------------"
echo "Merge policy: no gh merge from this step (default). See: $MERGE_POLICY_FILE"
if [ "$AUTO_MERGE" = "true" ]; then
  echo "Note: --auto-merge was passed; see merge-policy.txt and README for guarded auto-merge via create-pr.sh."
fi
echo "------------------------------------------------------------------"

if [ "$NOTIFY_TELEGRAM" = "true" ]; then
  "$SCRIPT_DIR/notify.sh" "$PROJECT_PATH" "$RUN_DIR" 2>/dev/null || true
fi

echo "HOCA run completed through commit. Hash recorded in $RUN_DIR/commit-hash.txt"
