#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: run-hoca-task.sh /path/to/project \"task\" [--issue-id ID] [--auto-merge] [--notify-telegram] [--model MODEL]"
  exit 1
fi

RAW_PROJECT_PATH="$1"
TASK="$2"
shift 2

ISSUE_ID=""
AUTO_MERGE="false"
NOTIFY_TELEGRAM="false"
REQUESTED_MODEL=""
MAX_REPAIR_ATTEMPTS="${HOCA_MAX_REPAIR_ATTEMPTS:-2}"
DEV_BRANCH="${HOCA_DEV_BRANCH:-}"
SYNC_DEV_BRANCH="${HOCA_SYNC_DEV_BRANCH:-true}"
AUTO_STAGE_REVIEWED_CHANGES="${HOCA_AUTO_STAGE_REVIEWED_CHANGES:-true}"
TASK_BASE_REF=""

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
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

run_definition_of_ready_check() {
  local dor_args=(
    "$SCRIPT_DIR/check-definition-of-ready.sh"
    "$1"
    "$2"
  )
  if [ -n "${3:-}" ]; then
    dor_args+=(--issue-id "$3")
  fi
  if [ -n "${4:-}" ]; then
    dor_args+=(--run-dir "$4")
  fi
  "${dor_args[@]}"
}

echo "Checking definition of ready..."
set +e
DOR_OUTPUT="$(run_definition_of_ready_check "$RAW_PROJECT_PATH" "$TASK" "$ISSUE_ID")"
DOR_EXIT=$?
set -e
printf '%s\n' "$DOR_OUTPUT"
if [ "$DOR_EXIT" -ne 0 ]; then
  if [ "$DOR_EXIT" -eq 2 ]; then
    echo "Stopping because the task needs clarification before HOCA can proceed safely." >&2
  else
    echo "Stopping because the task failed definition-of-ready checks." >&2
  fi
  exit "$DOR_EXIT"
fi

PROJECT_PATH="$(cd "$RAW_PROJECT_PATH" && pwd)"

record_run_artifact() {
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.run_artifacts "$@"
}

task_spec_path_for_run() {
  printf '%s\n' "$RUN_DIR/task-spec.json"
}

read_task_spec_field() {
  local field="$1"
  local fallback="$2"
  local spec_path
  spec_path="$(task_spec_path_for_run)"
  if [ -f "$spec_path" ] && command -v jq >/dev/null 2>&1; then
    jq -r --arg field "$field" --arg fallback "$fallback" '.[$field] // $fallback' "$spec_path"
  else
    printf '%s\n' "$fallback"
  fi
}

worker_task_prompt() {
  read_task_spec_field goal "$TASK"
}

original_task_prompt() {
  read_task_spec_field raw_request "$TASK"
}

generate_run_task_spec() {
  local generate_args=(
    "$SCRIPT_DIR/generate-task-spec.sh"
    "$PROJECT_PATH"
    "$TASK"
    "$PROJECT_PATH/$RUN_DIR"
    --run-id "$RUN_ID"
    --base-branch "$TASK_BASE_REF"
    --task-branch "$BRANCH"
    --max-total-rounds "$((MAX_REPAIR_ATTEMPTS + 1))"
  )
  if [ -n "$ISSUE_ID" ]; then
    generate_args+=(--issue-id "$ISSUE_ID")
  fi
  echo "Generating task spec..."
  "${generate_args[@]}"
}

if [ -n "$REQUESTED_MODEL" ]; then
  export HOCA_REQUESTED_MODEL="$REQUESTED_MODEL"
  case "$REQUESTED_MODEL" in
    openai/*|deepseek/*|gemini/*|anthropic/*|together_ai/*|openrouter/*)
      export LLM_MODEL="$REQUESTED_MODEL"
      ;;
    ollama/*)
      export LLM_MODEL="$REQUESTED_MODEL"
      export OLLAMA_MODEL="${REQUESTED_MODEL#ollama/}"
      ;;
    *)
      export OLLAMA_MODEL="$REQUESTED_MODEL"
      export LLM_MODEL="ollama/$REQUESTED_MODEL"
      ;;
  esac
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

is_dependency_lockfile_path() {
  case "$(basename "$1")" in
    package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|poetry.lock|Pipfile.lock|uv.lock|Cargo.lock|Gemfile.lock|composer.lock)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

checkout_dev_branch() {
  if [ -z "$DEV_BRANCH" ]; then
    TASK_BASE_REF="HEAD"
    echo "Development branch: not configured (HOCA_DEV_BRANCH is unset)"
    echo "Development branch sync: skipped"
    echo "Task branch base: current HEAD ($(git rev-parse --short HEAD))"
    return
  fi
  if [ "$CURRENT_BRANCH" = "$DEV_BRANCH" ]; then
    echo "Development branch: $DEV_BRANCH"
  else
    echo "Switching to development branch: $DEV_BRANCH"
    if ! git checkout "$DEV_BRANCH"; then
      echo "Unable to switch to configured development branch: $DEV_BRANCH" >&2
      exit 1
    fi
    CURRENT_BRANCH="$(git branch --show-current)"
  fi

  TASK_BASE_REF="$CURRENT_BRANCH"
  if [ "$SYNC_DEV_BRANCH" = "true" ]; then
    if git remote get-url origin >/dev/null 2>&1; then
      echo "Development branch sync: enabled"
      echo "Fetching latest development branch from origin: $DEV_BRANCH"
      if ! git fetch origin "$DEV_BRANCH"; then
        echo "Unable to fetch configured development branch from origin: $DEV_BRANCH" >&2
        exit 1
      fi
      if git rev-parse --verify "origin/${DEV_BRANCH}" >/dev/null 2>&1; then
        TASK_BASE_REF="origin/${DEV_BRANCH}"
        echo "Fetched development branch: $TASK_BASE_REF ($(git rev-parse --short "$TASK_BASE_REF"))"
        echo "Task branch base: $TASK_BASE_REF"
      else
        echo "Fetched origin/${DEV_BRANCH}, but the remote ref was not found." >&2
        exit 1
      fi
    else
      echo "Development branch sync: skipped (no origin remote configured)"
      echo "No origin remote configured; using local development branch: $DEV_BRANCH"
      echo "Task branch base: $TASK_BASE_REF ($(git rev-parse --short "$TASK_BASE_REF"))"
    fi
  else
    echo "Development branch sync: disabled (HOCA_SYNC_DEV_BRANCH=$SYNC_DEV_BRANCH)"
    echo "Task branch base: $TASK_BASE_REF ($(git rev-parse --short "$TASK_BASE_REF"))"
  fi
}

CURRENT_BRANCH="$(git branch --show-current)"
INITIAL_BRANCH="$CURRENT_BRANCH"
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

checkout_dev_branch
PRE_RUN_STATUS="$(git_status_short_for_task)"
if [ -n "$PRE_RUN_STATUS" ]; then
  echo "Working tree has existing changes after switching branches:"
  printf '%s\n' "$PRE_RUN_STATUS"
  echo "Stopping to avoid mixing unrelated human changes with agent changes."
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
printf '%s\n' "$TASK" > "$RUN_DIR/raw-task.txt"

echo "Recording definition-of-ready artifact..."
run_definition_of_ready_check "$RAW_PROJECT_PATH" "$TASK" "$ISSUE_ID" "$PROJECT_PATH/$RUN_DIR" >/dev/null

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
    record_run_artifact record-final "$RUN_DIR" >/dev/null 2>&1 || true
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

EARLY_INIT_STATUS_ARGS=(
  init-status "$RUN_DIR"
  --run-id "$RUN_ID"
  --task "$TASK"
  --max-total-rounds "$((MAX_REPAIR_ATTEMPTS + 1))"
  --auto-merge "$AUTO_MERGE"
  --notify-telegram "$NOTIFY_TELEGRAM"
  --requested-model "$REQUESTED_MODEL"
  --repo-root "$REPO_ROOT"
  --starting-branch "$INITIAL_BRANCH"
  --task-base-branch "$TASK_BASE_REF"
  --dev-branch "$DEV_BRANCH"
  --started-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
)
if [ -n "$ISSUE_ID" ]; then
  EARLY_INIT_STATUS_ARGS+=(--issue-id "$ISSUE_ID")
fi
record_run_artifact "${EARLY_INIT_STATUS_ARGS[@]}" >/dev/null 2>&1 || true

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
  record_run_artifact sync-status "$RUN_DIR" >/dev/null 2>&1 || true
}

sync_run_status() {
  record_run_artifact sync-status "$RUN_DIR" >/dev/null 2>&1 || true
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

echo "HOCA run started: $RUN_ID"

{
  echo "Repository root: $REPO_ROOT"
  echo "Current branch: $CURRENT_BRANCH"
  if [ -n "$DEV_BRANCH" ]; then
    echo "Development branch: $DEV_BRANCH"
  fi
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

echo "Creating branch: $BRANCH from $TASK_BASE_REF ($(git rev-parse --short "$TASK_BASE_REF"))"
git checkout -b "$BRANCH" "$TASK_BASE_REF"

generate_run_task_spec

INIT_STATUS_ARGS=(
  init-status "$RUN_DIR"
  --run-id "$RUN_ID"
  --task "$TASK"
  --max-total-rounds "$((MAX_REPAIR_ATTEMPTS + 1))"
  --auto-merge "$AUTO_MERGE"
  --notify-telegram "$NOTIFY_TELEGRAM"
  --requested-model "$REQUESTED_MODEL"
  --repo-root "$REPO_ROOT"
  --starting-branch "$INITIAL_BRANCH"
  --task-base-branch "$TASK_BASE_REF"
  --dev-branch "$DEV_BRANCH"
  --started-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
)
if [ -n "$ISSUE_ID" ]; then
  INIT_STATUS_ARGS+=(--issue-id "$ISSUE_ID")
fi
record_run_artifact "${INIT_STATUS_ARGS[@]}"

record_worker_attempt() {
  local round_number="$1"
  local status="${2:-completed}"
  record_run_artifact record-worker "$RUN_DIR" --round "$round_number" --status "$status" >/dev/null 2>&1 || true
  sync_run_status
}

record_validation_artifact() {
  local round_number="$1"
  record_run_artifact record-validation "$RUN_DIR" --round "$round_number" >/dev/null 2>&1 || true
  sync_run_status
}

record_manager_decision_artifact() {
  local round_number="$1"
  record_run_artifact record-decision "$RUN_DIR" --round "$round_number" >/dev/null 2>&1 || true
}

hermes_profiles_enabled() {
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
    'from hoca.config import load_config; import sys; sys.exit(0 if load_config().use_hermes_profiles else 1)' \
    >/dev/null 2>&1
}

run_openhands_phase() {
  local phase_label="${1:-implementation}"
  local round_number="${2:-1}"
  local repair_brief_path="${3:-}"
  local openhands_exit=0
  local used_worker_hermes="false"

  echo "Running OpenHands ($phase_label)..."
  set +e
  if hermes_profiles_enabled; then
    used_worker_hermes="true"
    echo "Routing worker attempt through run-worker-hermes.sh..."
    local worker_cmd=(
      "$SCRIPT_DIR/run-worker-hermes.sh"
      "$PROJECT_PATH"
      "$(task_spec_path_for_run)"
      "$RUN_DIR"
      "$round_number"
    )
    if [ -n "$repair_brief_path" ]; then
      worker_cmd+=(--repair-brief "$repair_brief_path")
    fi
    "${worker_cmd[@]}"
    openhands_exit=$?
  else
    local phase_task
    if [ -n "$repair_brief_path" ]; then
      phase_task="$(cat "$repair_brief_path")"
    else
      phase_task="$(worker_task_prompt)"
    fi
    "$SCRIPT_DIR/run-openhands-task.sh" "$PROJECT_PATH" "$phase_task" "$RUN_DIR"
    openhands_exit=$?
  fi
  set -e
  if [ "$openhands_exit" -ne 0 ]; then
    local failure_status="failed"
    if [ -f "$RUN_DIR/monitor-result.json" ] && command -v jq >/dev/null 2>&1; then
      STOP_REASON="$(jq -r '.stop_reason // "unknown"' "$RUN_DIR/monitor-result.json")"
      if [ "$STOP_REASON" != "completed" ]; then
        failure_status="blocked"
      fi
    fi
    if [ "$used_worker_hermes" != "true" ]; then
      record_worker_attempt "$round_number" "$failure_status"
    fi
    if [ "$failure_status" = "blocked" ]; then
      block_run "openhands_${STOP_REASON}" "OpenHands was stopped by the safety monitor ($STOP_REASON). Logs were saved in $RUN_DIR."
    fi
    fail_run "openhands_failed" "OpenHands failed with exit code $openhands_exit. Logs were saved in $RUN_DIR."
  fi
  if [ "$used_worker_hermes" = "true" ]; then
    sync_run_status
  else
    record_worker_attempt "$round_number" "completed"
  fi
}

WORKER_ROUND=1
run_openhands_phase "implementation" "$WORKER_ROUND"

path_is_secret_like() {
  local path="$1"
  local lower
  lower="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"
  local base
  base="$(basename "$lower")"
  case "$base" in
    *.example|*.sample|*.template)
      return 1
      ;;
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
    echo "$(original_task_prompt)"
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
    if [ -f "$RUN_DIR/openhands-review.txt" ]; then
      echo "Review feedback:"
      cat "$RUN_DIR/openhands-review.txt"
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

  VALIDATION_ROUND="$((repair_attempt + 1))"
  echo "Running tests..."
  set +e
  "$SCRIPT_DIR/run-tests.sh" "$PROJECT_PATH" "$RUN_DIR"
  TESTS_EXIT=$?
  set -e
  record_validation_artifact "$VALIDATION_ROUND"
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
    build_repair_task "tests_failed" "$repair_attempt" >/dev/null
    REPAIR_BRIEF_PATH="$RUN_DIR/repair-attempt-${repair_attempt}.md"
    WORKER_ROUND="$((repair_attempt + 1))"
    run_openhands_phase "test repair attempt $repair_attempt" "$WORKER_ROUND" "$REPAIR_BRIEF_PATH"
    check_openhands_changed_files
    continue
  fi

  echo "Running OpenHands review..."
  set +e
  HOCA_REVIEW_ROUND="$VALIDATION_ROUND" "$SCRIPT_DIR/review-with-openhands.sh" "$PROJECT_PATH" "$(worker_task_prompt)" "$RUN_DIR"
  REVIEW_EXIT=$?
  set -e
  record_manager_decision_artifact "$VALIDATION_ROUND"
  if [ "$REVIEW_EXIT" -eq 2 ]; then
    if [ "$repair_attempt" -ge "$MAX_REPAIR_ATTEMPTS" ]; then
      block_run "review_not_lgtm" "Review still did not return LGTM after $repair_attempt repair attempt(s). Human review is needed; see $RUN_DIR/openhands-review.txt."
    fi
    repair_attempt=$((repair_attempt + 1))
    update_status "repairing" "review_not_lgtm_attempt_${repair_attempt}"
    build_repair_task "review_not_lgtm" "$repair_attempt" >/dev/null
    REPAIR_BRIEF_PATH="$RUN_DIR/repair-attempt-${repair_attempt}.md"
    WORKER_ROUND="$((repair_attempt + 1))"
    run_openhands_phase "review repair attempt $repair_attempt" "$WORKER_ROUND" "$REPAIR_BRIEF_PATH"
    check_openhands_changed_files
    continue
  elif [ "$REVIEW_EXIT" -ne 0 ]; then
    if [ "$REVIEW_EXIT" -eq 4 ]; then
      block_run "review_blocked" "OpenHands review reported a blocked verdict. Human intervention is needed; see $RUN_DIR/openhands-review.txt."
    fi
    block_run "review_failed" "OpenHands review failed with exit code $REVIEW_EXIT. Human intervention may be needed; see $RUN_DIR/openhands-review.txt."
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

if [ ! -f "$INTENDED_FILE_LIST" ] && [ ! -f "$INTENDED_FILE_SOURCE" ] && [ "$AUTO_STAGE_REVIEWED_CHANGES" = "true" ]; then
  echo "Generating manager intended-file list from reviewed changed files..."
  cp "$RUN_DIR/changed-files.txt" "$INTENDED_FILE_LIST"
  printf '%s\n' "manager" > "$INTENDED_FILE_SOURCE"
  : > "$RUN_DIR/staging-justification.txt"
  while IFS= read -r changed_path || [ -n "$changed_path" ]; do
    [ -z "$changed_path" ] && continue
    if is_dependency_lockfile_path "$changed_path"; then
      printf '%s: dependency lockfile updated by package manager for reviewed dependency changes.\n' "$changed_path" >> "$RUN_DIR/staging-justification.txt"
    fi
  done < "$RUN_DIR/changed-files.txt"
fi

if [ -f "$INTENDED_FILE_LIST" ] || [ -f "$INTENDED_FILE_SOURCE" ]; then
  echo "Safe staging artifacts detected. Attempting automatic safe staging..."
  if HOCA_REVIEW_ROUND="$((repair_attempt + 1))" "$SCRIPT_DIR/safe-stage-after-review.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "$INTENDED_FILE_LIST"; then
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

record_run_artifact record-final "$RUN_DIR" >/dev/null 2>&1 || true
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
  HOCA_REVIEW_ROUND="$((repair_attempt + 1))" "$SCRIPT_DIR/create-pr.sh" "$PROJECT_PATH" "$TASK" "$RUN_DIR" "${PR_ARGS[@]}"
  update_status "pr_created" "pull_request_created"
  "$SCRIPT_DIR/generate-task-report.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null
  "$SCRIPT_DIR/notify.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null 2>&1 || true
  if [ -d ".hoca-runtime" ] && [ "${HOCA_KEEP_RUNTIME:-false}" != "true" ]; then
    echo "Cleaning up .hoca-runtime..."
    rm -rf ".hoca-runtime"
  fi
  echo "HOCA run completed through pull request creation."
else
  echo "HOCA run completed up to review. Human staging required."
fi
