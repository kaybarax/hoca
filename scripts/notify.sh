#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  notify.sh TYPE "task text" [PR_URL] [--telegram]
  notify.sh /path/to/project /path/to/run-dir

TYPE must be one of: complete, blocked, failed, needs-review.

Telegram is sent only when enabled with --telegram, HOCA_NOTIFY_TELEGRAM=true,
or notify_telegram=true in run-dir/status.json. TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID must also be set.
EOF
}

is_truthy() {
  case "${1:-}" in
    true|TRUE|1|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

load_env_file() {
  local env_file="$1"
  [ -f "$env_file" ] || return 0

  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
    esac

    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      local key="${line%%=*}"
      local value="${line#*=}"
      value="${value%$'\r'}"
      if [ "${value#\"}" != "$value" ] && [ "${value%\"}" != "$value" ]; then
        value="${value#\"}"
        value="${value%\"}"
      elif [ "${value#\'}" != "$value" ] && [ "${value%\'}" != "$value" ]; then
        value="${value#\'}"
        value="${value%\'}"
      fi
      if [ -z "${!key+x}" ]; then
        export "$key=$value"
      fi
    fi
  done < "$env_file"
}

json_value() {
  local file="$1"
  local key="$2"
  if command -v jq >/dev/null 2>&1 && [ -f "$file" ]; then
    jq -r --arg key "$key" '.[$key] // empty' "$file"
  fi
}

notification_message() {
  case "$1" in
    complete) echo "HOCA task complete." ;;
    blocked) echo "HOCA task blocked." ;;
    failed) echo "HOCA task failed." ;;
    needs-review) echo "HOCA task needs review." ;;
    *)
      echo "Unknown notification type: $1" >&2
      usage
      exit 1
      ;;
  esac
}

send_macos_notification() {
  local body="$1"
  if command -v osascript >/dev/null 2>&1; then
    if osascript - "$body" "HOCA" <<'APPLESCRIPT'
on run argv
  display notification (item 1 of argv) with title (item 2 of argv)
end run
APPLESCRIPT
    then
      MACOS_RESULT="sent"
    else
      MACOS_RESULT="failed"
      echo "macOS notification failed." >&2
    fi
  else
    MACOS_RESULT="skipped"
  fi
}

send_telegram_notification() {
  local body="$1"
  if ! command -v curl >/dev/null 2>&1; then
    TELEGRAM_RESULT="skipped_curl_missing"
    echo "Telegram notification skipped: curl is not installed." >&2
    return 0
  fi
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    TELEGRAM_RESULT="skipped_config_missing"
    echo "Telegram notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing." >&2
    return 0
  fi

  if curl --fail --silent --show-error \
    --request POST \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${body}" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null
  then
    TELEGRAM_RESULT="sent"
  else
    TELEGRAM_RESULT="failed"
    echo "Telegram notification failed." >&2
  fi
}

save_notification_result() {
  [ -n "$RUN_DIR" ] || return 0

  local result_file="$RUN_DIR/notification-result.txt"
  {
    echo "type=$NOTIFY_TYPE"
    echo "title=HOCA"
    echo "message=$BASE_MESSAGE"
    echo "task=$TASK_TEXT"
    echo "pr_url=$PR_URL"
    echo "macos=$MACOS_RESULT"
    echo "telegram=$TELEGRAM_RESULT"
    echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$result_file"
}

if [ "$#" -lt 2 ]; then
  usage
  exit 1
fi

NOTIFY_TYPE="$1"
TASK_TEXT="$2"
PR_URL="${3:-}"
TELEGRAM_REQUESTED="false"
PROJECT_PATH=""
RUN_DIR=""
MACOS_RESULT="not_attempted"
TELEGRAM_RESULT="not_enabled"

if [ -d "$1" ] && [ -d "$2" ]; then
  PROJECT_PATH="$(cd "$1" && pwd)"
  RUN_DIR="$(cd "$2" && pwd)"
  STATUS_FILE="$RUN_DIR/status.json"
  NOTIFY_TYPE="$(json_value "$STATUS_FILE" status)"
  TASK_TEXT="$(json_value "$STATUS_FILE" task)"
  PR_URL="$(json_value "$STATUS_FILE" pr_url)"
  TELEGRAM_REQUESTED="$(json_value "$STATUS_FILE" notify_telegram)"

  case "$NOTIFY_TYPE" in
    committed|staged|no_changes) NOTIFY_TYPE="complete" ;;
    needs_human_staging) NOTIFY_TYPE="needs-review" ;;
    failed) NOTIFY_TYPE="failed" ;;
    ""|started) NOTIFY_TYPE="needs-review" ;;
    *) NOTIFY_TYPE="blocked" ;;
  esac
else
  shift 2
  if [ "${1:-}" != "" ] && [ "${1:-}" != "--telegram" ]; then
    PR_URL="$1"
    shift
  fi
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --telegram)
        TELEGRAM_REQUESTED="true"
        shift
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage
        exit 1
        ;;
    esac
  done
fi

if [ -n "$PROJECT_PATH" ]; then
  load_env_file "$PROJECT_PATH/.env"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  load_env_file "$REPO_ROOT/.env"
fi

BASE_MESSAGE="$(notification_message "$NOTIFY_TYPE")"
BODY="$BASE_MESSAGE"
if [ -n "$TASK_TEXT" ]; then
  BODY="$BODY Task: $TASK_TEXT"
fi
if [ -n "$PR_URL" ]; then
  BODY="$BODY PR: $PR_URL"
fi

send_macos_notification "$BODY"

if is_truthy "$TELEGRAM_REQUESTED" || is_truthy "${HOCA_NOTIFY_TELEGRAM:-}"; then
  send_telegram_notification "$BODY"
fi

save_notification_result
echo "$BODY"
