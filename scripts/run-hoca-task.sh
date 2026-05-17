#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: run-hoca-task.sh /path/to/project \"task\" [--issue-id ID] [--auto-merge] [--notify-telegram] [--model MODEL]"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
shift 2

ISSUE_ID=""
AUTO_MERGE="false"
NOTIFY_TELEGRAM="false"
REQUESTED_MODEL=""
MAX_REPAIR_ATTEMPTS="${HOCA_MAX_REPAIR_ATTEMPTS:-2}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --issue-id)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --issue-id"
        exit 1
      fi
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
    --model)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --model"
        exit 1
      fi
      REQUESTED_MODEL="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "$REQUESTED_MODEL" ]; then
  export HOCA_REQUESTED_MODEL="$REQUESTED_MODEL"
  export OLLAMA_MODEL="$REQUESTED_MODEL"
  export LLM_MODEL="ollama/$REQUESTED_MODEL"
  export AIDER_MODEL="ollama_chat/$REQUESTED_MODEL"
fi

cd "$PROJECT_PATH"

echo "Validating target repository..."
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH"
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"

git_status_short_for_task() {
  git status --short | while IFS= read -r status_line || [ -n "$status_line" ]; do
    local path="${status_line#???}"
    case "$path" in
      .hoca-runtime|.hoca-runtime/*) continue ;;
    esac
    printf '%s\n' "$status_line"
  done
}

changed_files_for_task() {
  git_status_short_for_task | sed 's/^...//'
}

CURRENT_BRANCH="$(git branch --show-current)"
PRE_RUN_STATUS="$(git_status_short_for_task)"

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
    "$SCRIPT_DIR/notify.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null 2>&1 || true
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
  "requested_model": "$REQUESTED_MODEL",
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
TASK_STATUS="$(git_status_short_for_task)"
if [ -n "$TASK_STATUS" ]; then
  echo "Working tree has existing changes:"
  printf '%s\n' "$TASK_STATUS"
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

run_openhands_phase() {
  local phase_task="$1"
  local phase_label="${2:-implementation}"

  echo "Running OpenHands ($phase_label)..."
  set +e
  "$SCRIPT_DIR/run-openhands-task.sh" "$PROJECT_PATH" "$phase_task" "$RUN_DIR"
  local openhands_exit=$?
  set -e
  if [ "$openhands_exit" -ne 0 ]; then
    if [ -f "$RUN_DIR/monitor-result.json" ] && command -v jq >/dev/null 2>&1; then
      STOP_REASON="$(jq -r '.stop_reason // "unknown"' "$RUN_DIR/monitor-result.json")"
      if [ "$STOP_REASON" != "completed" ]; then
        block_run "openhands_${STOP_REASON}" "OpenHands was stopped by the safety monitor ($STOP_REASON). Logs were saved in $RUN_DIR."
      fi
    fi
    fail_run "openhands_failed" "OpenHands failed with exit code $openhands_exit. Logs were saved in $RUN_DIR."
  fi
}

run_openhands_phase "$TASK" "implementation"

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

check_openhands_changed_files() {
  changed_files_for_task > "$RUN_DIR/changed-files-after-openhands.txt"
  while IFS= read -r changed_path || [ -n "$changed_path" ]; do
    [ -z "$changed_path" ] && continue
    if path_is_secret_like "$changed_path"; then
      printf '%s\n' "$changed_path" > "$RUN_DIR/secret-detected.txt"
      fail_run "secret_detected" "Secret-like changed file detected after OpenHands: $changed_path. Stopping immediately."
    fi
  done < "$RUN_DIR/changed-files-after-openhands.txt"
}

check_openhands_changed_files

build_repair_task() {
  local reason="$1"
  local attempt="$2"
  local repair_file="$RUN_DIR/repair-attempt-${attempt}.md"

  {
    echo "Continue this HOCA task by fixing the current repository changes; do not start over."
    echo ""
    echo "Original task:"
    echo "$TASK"
    echo ""
    echo "Repair reason: $reason"
    echo "Repair attempt: $attempt of $MAX_REPAIR_ATTEMPTS"
    echo ""
    echo "Current git status:"
    git status --short
    echo ""
    echo "Current diff:"
    git diff
    echo ""
    if [ -f "$RUN_DIR/tests-summary.md" ]; then
      echo "Test summary:"
      cat "$RUN_DIR/tests-summary.md"
      echo ""
    fi
    if [ -f "$RUN_DIR/failed-command.txt" ]; then
      echo "Failed command:"
      cat "$RUN_DIR/failed-command.txt"
      echo ""
    fi
    if [ -f "$RUN_DIR/tests-output.log" ]; then
      echo "Recent test output:"
      tail -n 120 "$RUN_DIR/tests-output.log"
      echo ""
    fi
    if [ -f "$RUN_DIR/tests-stderr.log" ]; then
      echo "Recent test stderr:"
      tail -n 120 "$RUN_DIR/tests-stderr.log"
      echo ""
    fi
    if [ -f "$RUN_DIR/aider-review.txt" ]; then
      echo "Aider review feedback:"
      cat "$RUN_DIR/aider-review.txt"
      echo ""
    fi
    echo "Fix only issues needed to make validation and review pass. If the failure is caused by missing local services, missing dependencies, credentials, or another human-only environment problem, explain that clearly and make no unrelated changes."
  } > "$repair_file"

  cat "$repair_file"
}

repair_attempt=0

while true; do
  if [ -z "$(git_status_short_for_task)" ]; then
    echo "No changes produced."
    update_status "no_changes"
    exit 0
  fi

  echo "Running tests..."
  set +e
  "$SCRIPT_DIR/run-tests.sh" "$PROJECT_PATH" "$RUN_DIR"
  TESTS_EXIT=$?
  set -e
  if [ "$TESTS_EXIT" -ne 0 ]; then
    FAILURE_TYPE=""
    if [ -f "$RUN_DIR/tests-summary.md" ]; then
      FAILURE_TYPE="$(awk -F': ' '/Failure type/ { gsub(/\r/, "", $2); print $2; exit }' "$RUN_DIR/tests-summary.md" | tr -d '*')"
    fi
    if [ "$FAILURE_TYPE" = "environment" ] || [ "$FAILURE_TYPE" = "pre-existing" ]; then
      block_run "tests_${FAILURE_TYPE}" "Tests failed due to $FAILURE_TYPE conditions. Human intervention is needed; see $RUN_DIR/tests-summary.md."
    fi
    if [ "$repair_attempt" -ge "$MAX_REPAIR_ATTEMPTS" ]; then
      fail_run "tests_failed" "Tests still failed after $repair_attempt repair attempt(s). Human review is needed; see $RUN_DIR/tests-summary.md."
    fi
    repair_attempt=$((repair_attempt + 1))
    update_status "repairing" "tests_failed_attempt_${repair_attempt}"
    REPAIR_TASK="$(build_repair_task "tests_failed" "$repair_attempt")"
    run_openhands_phase "$REPAIR_TASK" "test repair attempt $repair_attempt"
    check_openhands_changed_files
    continue
  fi

  echo "Running Aider review..."
  set +e
  "$SCRIPT_DIR/review-with-aider.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR"
  AIDER_EXIT=$?
  set -e
  if [ "$AIDER_EXIT" -eq 2 ] || { [ "$AIDER_EXIT" -eq 0 ] && ! grep -q "LGTM" "$RUN_DIR/aider-review.txt"; }; then
    if [ "$repair_attempt" -ge "$MAX_REPAIR_ATTEMPTS" ]; then
      block_run "aider_not_lgtm" "Aider still did not return LGTM after $repair_attempt repair attempt(s). Human review is needed; see $RUN_DIR/aider-review.txt."
    fi
    repair_attempt=$((repair_attempt + 1))
    update_status "repairing" "aider_not_lgtm_attempt_${repair_attempt}"
    REPAIR_TASK="$(build_repair_task "aider_not_lgtm" "$repair_attempt")"
    run_openhands_phase "$REPAIR_TASK" "Aider repair attempt $repair_attempt"
    check_openhands_changed_files
    continue
  elif [ "$AIDER_EXIT" -ne 0 ]; then
    block_run "aider_failed" "Aider failed with exit code $AIDER_EXIT. Human intervention may be needed; see $RUN_DIR/aider-review.txt and aider-stderr.log."
  fi

  if ! grep -q "LGTM" "$RUN_DIR/aider-review.txt"; then
    block_run "aider_not_lgtm" "Aider did not return LGTM. Stopping before commit."
  fi

  break
done

echo "Inspecting changed files..."
git_status_short_for_task | tee "$RUN_DIR/git-status.txt"
git diff > "$RUN_DIR/git-diff.patch"
changed_files_for_task > "$RUN_DIR/changed-files.txt"

if [ -z "$(git_status_short_for_task)" ]; then
  echo "No changes produced."
  update_status "no_changes"
  exit 0
fi

INTENDED_FILE_LIST="$RUN_DIR/intended-files.txt"
INTENDED_FILE_SOURCE="$RUN_DIR/intended-files-source.txt"

if [ -f "$INTENDED_FILE_LIST" ] || [ -f "$INTENDED_FILE_SOURCE" ]; then
  echo "Safe staging artifacts detected. Attempting automatic safe staging..."
  if "$SCRIPT_DIR/safe-stage-after-review.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "$INTENDED_FILE_LIST"; then
    update_status "staged" "safe_staging_completed"
  else
    STAGING_EXIT=$?
    update_status "needs_human_staging" "safe_staging_failed"
    exit "$STAGING_EXIT"
  fi
else
  echo "Manual selective staging is required."
  echo "Stopping before staging; changed files are recorded in $RUN_DIR/changed-files.txt"
  echo "Diff is recorded in $RUN_DIR/git-diff.patch"
  update_status "needs_human_staging" "selective_staging_required"
fi

"$SCRIPT_DIR/generate-task-report.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null

if [ -s "$RUN_DIR/staged-files.txt" ]; then
  echo "Creating commit from safely staged files..."
  COMMIT_ARGS=()
  if [ -n "$ISSUE_ID" ]; then
    COMMIT_ARGS=(--issue-id "$ISSUE_ID")
  fi
  "$SCRIPT_DIR/commit-after-staging.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "${COMMIT_ARGS[@]}"
  update_status "committed" "commit_created"

  echo "Creating pull request..."
  PR_ARGS=()
  if [ -n "$ISSUE_ID" ]; then
    PR_ARGS=(--issue-id "$ISSUE_ID")
  fi
  "$SCRIPT_DIR/create-pr.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "${PR_ARGS[@]}"
  update_status "pr_created" "pull_request_created"
  "$SCRIPT_DIR/generate-task-report.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null
  "$SCRIPT_DIR/notify.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null 2>&1 || true
  echo "HOCA run completed through pull request creation."
else
  echo "HOCA run completed up to review. Human staging required."
fi
