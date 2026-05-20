#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: commit-after-staging.sh /path/to/project \"task\" /path/to/run-dir [--issue-id ID]" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$(cd "$3" && pwd)"
shift 3

ISSUE_ID=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --issue-id)
      ISSUE_ID="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

cd "$PROJECT_PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || REPO_ROOT="$PROJECT_PATH"
# shellcheck source=lib/hoca-security.sh
source "$SCRIPT_DIR/lib/hoca-security.sh"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH" >&2
  exit 1
fi

if [ ! -f "$RUN_DIR/staged-files.txt" ] || [ ! -s "$RUN_DIR/staged-files.txt" ]; then
  echo "Missing or empty staged-files.txt in run directory; run safe staging first." >&2
  exit 1
fi

STAGED_SORTED="$RUN_DIR/.staged-files-sorted.txt"
CACHED_SORTED="$RUN_DIR/.cached-names-sorted.txt"
sed '/^[[:space:]]*$/d' "$RUN_DIR/staged-files.txt" | sort -u > "$STAGED_SORTED"
git diff --cached --name-only | sort -u > "$CACHED_SORTED"
if [ ! -s "$STAGED_SORTED" ] || ! diff -q "$STAGED_SORTED" "$CACHED_SORTED" >/dev/null; then
  echo "staged-files.txt must match non-empty git diff --cached --name-only exactly." >&2
  exit 1
fi

if ! git diff --cached --check; then
  echo "Staged diff failed whitespace/conflict checks (git diff --cached --check)." >&2
  exit 1
fi

echo "=== Pre-commit: git status --short ==="
git status --short | tee "$RUN_DIR/pre-commit-git-status-short.txt"

echo "=== Pre-commit: git diff --cached ==="
git diff --cached > "$RUN_DIR/pre-commit-git-diff-cached.txt"
git diff --cached --stat

assert_cached_path_safe() {
  local path="$1"
  if ! hoca_validate_staging_path "$REPO_ROOT" "$path"; then
    case "$path" in
      .hoca-runtime/*|.hoca-runtime)
        echo "Refusing commit: forbidden staged path (.hoca-runtime): $path" >&2
        ;;
      *)
        if hoca_path_is_secret_like "$path"; then
          echo "Refusing commit: forbidden staged path (secret-like name): $path" >&2
        else
          echo "Refusing commit: forbidden staged path: $path" >&2
        fi
        ;;
    esac
    return 1
  fi
  return 0
}

while IFS= read -r path || [ -n "$path" ]; do
  [ -z "$path" ] && continue
  assert_cached_path_safe "$path" || exit 1
done < "$CACHED_SORTED"

INTENDED_NORM="$RUN_DIR/intended-files.normalized.txt"
if [ -f "$INTENDED_NORM" ]; then
  INTENDED_SORTED="$RUN_DIR/.intended-for-commit-check.txt"
  sort -u "$INTENDED_NORM" > "$INTENDED_SORTED"
  if ! diff -q "$INTENDED_SORTED" "$CACHED_SORTED" >/dev/null; then
    echo "Staged index does not match intended-files.normalized.txt from safe staging." >&2
    exit 1
  fi
fi

TASK_ONELINE="$(printf '%s' "$TASK" | tr '\n\r' '  ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/[[:space:]]\{2,\}/ /g')"
if [ -z "$TASK_ONELINE" ]; then
  echo "Task text is empty; cannot build commit message." >&2
  exit 1
fi

LOWER_TASK="$(printf '%s' "$TASK_ONELINE" | tr '[:upper:]' '[:lower:]')"

if printf '%s' "$TASK_ONELINE" | grep -qiE \
  '(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer[[:space:]]+[a-z0-9_-]{10,}|password[[:space:]]*=[[:space:]]*[^[:space:]]|-----BEGIN[[:space:]]+(RSA|OPENSSH|EC)[[:space:]]+PRIVATE[[:space:]]+KEY-----)'; then
  echo "Task text looks like it may contain secrets; refusing to generate a commit message automatically." >&2
  exit 1
fi

CONVENTIONAL_PREFIX="feat"
case "$LOWER_TASK" in
  fix:*|fix[[:space:]]*|*fix[[:space:]]bug*|*bug[[:space:]]fix*)
    CONVENTIONAL_PREFIX="fix"
    ;;
  docs:*|doc:*|document*|*readme*|*changelog*)
    CONVENTIONAL_PREFIX="docs"
    ;;
  test:*|tests:*|*unit[[:space:]]test*|*add[[:space:]]test*|*testing*)
    CONVENTIONAL_PREFIX="test"
    ;;
  refactor:*|*refactor*)
    CONVENTIONAL_PREFIX="refactor"
    ;;
  chore:*|*dependenc*|*bump[[:space:]]*|*lockfile*|*depen[[:space:]]*)
    CONVENTIONAL_PREFIX="chore"
    ;;
esac

DESC="$TASK_ONELINE"
case "$DESC" in
  fix:*|feat:*|docs:*|test:*|refactor:*|chore:*)
    DESC="${DESC#*:}"
    DESC="$(printf '%s' "$DESC" | sed 's/^[[:space:]]*//')"
    ;;
esac

if [ -z "$DESC" ]; then
  DESC="$TASK_ONELINE"
fi

SUBJECT="${CONVENTIONAL_PREFIX}: ${DESC}"
if [ -n "$ISSUE_ID" ]; then
  SUBJECT="${SUBJECT} (#${ISSUE_ID})"
fi

MAX_SUBJECT_LEN=100
if [ "${#SUBJECT}" -gt "$MAX_SUBJECT_LEN" ]; then
  TRUNC_LEN=$((MAX_SUBJECT_LEN - 3))
  SUBJECT="${SUBJECT:0:$TRUNC_LEN}..."
fi

COMMIT_MSG_FILE="$RUN_DIR/commit-message.txt"
printf '%s\n' "$SUBJECT" > "$COMMIT_MSG_FILE"

if ! git commit -F "$COMMIT_MSG_FILE"; then
  echo "git commit failed." >&2
  exit 1
fi

git rev-parse HEAD > "$RUN_DIR/commit-hash.txt"
echo "Committed $(cat "$RUN_DIR/commit-hash.txt") with message from $COMMIT_MSG_FILE"
