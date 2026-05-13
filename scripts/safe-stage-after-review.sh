#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: safe-stage-after-review.sh /path/to/project \"task\" /path/to/run-dir /path/to/intended-file-list" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$3"
INTENDED_FILE_LIST="$4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_PATH"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "Not a git repository: $PROJECT_PATH" >&2
  exit 1
}

if [ ! -f "$RUN_DIR/aider-review.txt" ] || ! grep -q "LGTM" "$RUN_DIR/aider-review.txt"; then
  echo "Refusing safe staging before an Aider review returns LGTM." >&2
  exit 1
fi

if [ ! -f "$INTENDED_FILE_LIST" ]; then
  echo "Intended file list is required for automatic safe staging: $INTENDED_FILE_LIST" >&2
  exit 1
fi

SOURCE_FILE="$RUN_DIR/intended-files-source.txt"
if [ ! -f "$SOURCE_FILE" ]; then
  echo "Intended file list must identify its producer in $SOURCE_FILE." >&2
  echo "Expected producer: manager or reviewer." >&2
  exit 1
fi

SOURCE="$(tr '[:upper:]' '[:lower:]' < "$SOURCE_FILE" | tr -d '[:space:]')"
case "$SOURCE" in
  manager|reviewer) ;;
  *)
    echo "Intended file list producer must be manager or reviewer; got: $SOURCE" >&2
    exit 1
    ;;
esac

NORMALIZED_LIST="$RUN_DIR/intended-files.normalized.txt"
CHANGED_LIST="$RUN_DIR/changed-files.normalized.txt"
UNACCOUNTED_LIST="$RUN_DIR/unaccounted-changed-files.txt"
UNEXPECTED_LIST="$RUN_DIR/unexpected-intended-files.txt"

normalize_file_list() {
  sed 's/#.*$//' "$1" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//' | awk 'NF' | sort -u
}

normalize_file_list "$INTENDED_FILE_LIST" > "$NORMALIZED_LIST"

if [ ! -s "$NORMALIZED_LIST" ]; then
  echo "Intended file list is empty after removing comments and blank lines." >&2
  exit 1
fi

git status --short | awk '{print $NF}' | sort -u > "$CHANGED_LIST"

if [ ! -s "$CHANGED_LIST" ]; then
  echo "No changed files to stage."
  exit 0
fi

comm -23 "$CHANGED_LIST" "$NORMALIZED_LIST" > "$UNACCOUNTED_LIST"
if [ -s "$UNACCOUNTED_LIST" ]; then
  echo "Changed files not accounted for by intended file list:" >&2
  cat "$UNACCOUNTED_LIST" >&2
  exit 1
fi

comm -13 "$CHANGED_LIST" "$NORMALIZED_LIST" > "$UNEXPECTED_LIST"
if [ -s "$UNEXPECTED_LIST" ]; then
  echo "Intended file list includes files that are not changed:" >&2
  cat "$UNEXPECTED_LIST" >&2
  exit 1
fi

JUSTIFICATION_FILE="$RUN_DIR/staging-justification.txt"
TASK_TOKENS_FILE="$RUN_DIR/task-tokens.txt"

printf '%s\n' "$TASK" \
  | tr '[:upper:]' '[:lower:]' \
  | tr -cs 'a-z0-9' '\n' \
  | awk 'length($0) >= 4 && $0 !~ /^(task|this|that|with|from|into|file|files|change|changes|update|implement|create|make|fix|safe|stage|staging)$/ { print }' \
  | sort -u > "$TASK_TOKENS_FILE"

require_justification() {
  local reason="$1"
  local file="$2"
  if [ ! -s "$JUSTIFICATION_FILE" ]; then
    echo "Refusing to stage $file: $reason requires justification in $JUSTIFICATION_FILE." >&2
    return 1
  fi
  if ! grep -Fq "$file" "$JUSTIFICATION_FILE"; then
    echo "Refusing to stage $file: $reason is not justified in $JUSTIFICATION_FILE." >&2
    return 1
  fi
}

is_generated_file() {
  case "$1" in
    *.min.js|*.min.css|*.generated.*|*.gen.*|*/generated/*|*/__generated__/*|*.egg-info/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_dependency_lockfile() {
  case "$(basename "$1")" in
    package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|poetry.lock|Pipfile.lock|uv.lock|Cargo.lock|Gemfile.lock|composer.lock)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_runtime_lock_file() {
  case "$1" in
    *.lock|*.lock.json|*.pid|.hoca-runtime/runs/*.lock)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_migration_file() {
  case "$1" in
    migrations/*|*/migrations/*|db/migrate/*|*/db/migrate/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_infrastructure_file() {
  case "$1" in
    .github/workflows/*|Dockerfile|docker-compose*.yml|docker-compose*.yaml|terraform/*|*.tf|k8s/*|kubernetes/*|charts/*|helm/*|vercel.json)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

matches_task_context() {
  local file="$1"
  local lower_file
  lower_file="$(printf '%s' "$file" | tr '[:upper:]' '[:lower:]')"

  if [ ! -s "$TASK_TOKENS_FILE" ]; then
    return 0
  fi

  while IFS= read -r token || [ -n "$token" ]; do
    if [[ "$lower_file" == *"$token"* ]]; then
      return 0
    fi
  done < "$TASK_TOKENS_FILE"

  if [ -s "$JUSTIFICATION_FILE" ] && grep -Fq "$file" "$JUSTIFICATION_FILE"; then
    return 0
  fi

  return 1
}

UNSAFE=0
while IFS= read -r file || [ -n "$file" ]; do
  if ! matches_task_context "$file"; then
    echo "Refusing to stage $file: intended file does not match task keywords and has no justification." >&2
    UNSAFE=1
  fi

  if is_dependency_lockfile "$file"; then
    require_justification "dependency lockfile change" "$file" || UNSAFE=1
  elif is_runtime_lock_file "$file"; then
    echo "Refusing to stage lock file: $file" >&2
    UNSAFE=1
    continue
  fi

  if is_generated_file "$file"; then
    require_justification "generated file change" "$file" || UNSAFE=1
  fi

  if is_migration_file "$file"; then
    require_justification "migration change" "$file" || UNSAFE=1
  fi

  if is_infrastructure_file "$file"; then
    require_justification "infrastructure change" "$file" || UNSAFE=1
  fi
done < "$NORMALIZED_LIST"

if [ "$UNSAFE" -ne 0 ]; then
  echo "Aborting safe staging because one or more files failed policy checks." >&2
  exit 1
fi

"$SCRIPT_DIR/stage-safe-files.sh" "$REPO_ROOT" "$NORMALIZED_LIST" > "$RUN_DIR/staged-diff.patch"

git diff --cached --check
git diff --cached --name-only > "$RUN_DIR/staged-files.txt"

if [ ! -s "$RUN_DIR/staged-files.txt" ]; then
  echo "No files were staged." >&2
  exit 1
fi

echo "Safe staging completed for files:"
cat "$RUN_DIR/staged-files.txt"
