#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: kanban-run.sh /path/to/project \"task\""
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"

if ! command -v hermes >/dev/null 2>&1; then
  echo "Error: hermes is not installed."
  echo "Install Hermes Agent to use Kanban features: https://github.com/NousResearch/hermes-agent"
  exit 1
fi

if ! hermes kanban -h >/dev/null 2>&1; then
  echo "Error: Hermes Kanban is not available in the installed version of Hermes."
  echo "Upgrade Hermes Agent to a version that supports Kanban boards."
  exit 1
fi

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH"
  exit 1
fi

REPO_SLUG="$(
  basename "$PROJECT_PATH" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+|-+$//g'
)"
BOARD_NAME="hoca:${REPO_SLUG}"
RUN_ID="hoca-$(date -u +%Y%m%dT%H%M%SZ)"
SHORT_GOAL="${TASK%%$'\n'*}"
PARENT_TITLE="HOCA: $SHORT_GOAL"
if [ "${#PARENT_TITLE}" -gt 80 ]; then
  PARENT_TITLE="${PARENT_TITLE:0:77}..."
fi
WORKSPACE="dir:${PROJECT_PATH}"
RUN_ROOT=".hoca-runtime/runs/${RUN_ID}"

PARENT_BODY="$(cat <<EOF
## HOCA Kanban Parent Task Contract

human_request: |
  ${TASK}
run_id: ${RUN_ID}
repo_path: ${PROJECT_PATH}
workspace: ${WORKSPACE}
current_round: 0
max_total_rounds: \${HOCA_MAX_TOTAL_ROUNDS:-3}
round_state: triage

### Role Contract

- Parent task owner: hoca-manager.
- Worker children use title pattern: implement r<N> or repair r<N>.
- Reviewer children use title pattern: review r<N>.
- Every child links to this parent and includes run_id, round, task-spec or repair brief, and artifact pointers.
- Worker and reviewer profiles exchange context only through this board, structured run artifacts, diffs, and Kanban comments.
- Do not rely on private shared memory between worker and reviewer profiles.

### Run Artifact Links

- task_spec: ${RUN_ROOT}/task-spec.json
- worker_attempts: ${RUN_ROOT}/attempts/worker-attempt-<round>.json
- validation_reports: ${RUN_ROOT}/validation/validation-report-<round>.json
- review_reports: ${RUN_ROOT}/reviews/review-report-<round>.json
- manager_decisions: ${RUN_ROOT}/decisions/manager-decision-<round>.json
- final_state: ${RUN_ROOT}/final-state.json

### Comment Protocol

Use [spec], [artifact], [validation], [decision], [round], [escalation], and [pr]
comments with repo-relative artifact paths so the board can reconstruct the run
story without embedding secrets or large logs.
EOF
)"

echo "Creating HOCA Kanban task on board: $BOARD_NAME"
echo "Task: $TASK"
echo ""

TASK_JSON="$(
  hermes kanban --board "$BOARD_NAME" create "$PARENT_TITLE" \
  --body "$PARENT_BODY" \
  --assignee hoca-manager \
  --workspace "$WORKSPACE" \
  --triage \
  --idempotency-key "$RUN_ID" \
  --created-by hoca \
  --skill hoca-manager \
  --json \
  2>&1
)" || {
    echo ""
    echo "Failed to create Kanban task."
    echo "$TASK_JSON"
    echo "Ensure the board exists: hoca kanban-init $PROJECT_PATH"
    exit 1
  }

KANBAN_TASK_ID="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))' <<<"$TASK_JSON"
)"

if [ -z "$KANBAN_TASK_ID" ]; then
  echo "Failed to read created Kanban task id from Hermes response."
  echo "$TASK_JSON"
  exit 1
fi

hermes kanban --board "$BOARD_NAME" comment "$KANBAN_TASK_ID" \
  "[round] queued for triage; run_id=${RUN_ID}; artifacts will live under ${RUN_ROOT}" \
  --author hoca-manager >/dev/null

echo ""
echo "Kanban task created: $KANBAN_TASK_ID"
echo "Board: $BOARD_NAME"
echo "Run ID: $RUN_ID"
echo "Assignee: hoca-manager"
echo ""
echo "The manager profile will pick up this task from the board."
echo "Use 'hoca kanban-watch' to monitor progress."
