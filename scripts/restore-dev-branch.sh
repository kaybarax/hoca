#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: restore-dev-branch.sh /path/to/project [--dev-branch NAME] [--initial-branch NAME] [--dry-run]" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
shift

DEV_BRANCH=""
INITIAL_BRANCH=""
DRY_RUN="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev-branch)
      DEV_BRANCH="$2"
      shift 2
      ;;
    --initial-branch)
      INITIAL_BRANCH="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ "${HOCA_RESTORE_DEV_BRANCH:-true}" != "true" ]; then
  echo "Development branch restore: disabled (HOCA_RESTORE_DEV_BRANCH=false)"
  exit 0
fi

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH" >&2
  exit 1
fi

target_branch() {
  if [ -n "$DEV_BRANCH" ]; then
    printf '%s\n' "$DEV_BRANCH"
  elif [ -n "$INITIAL_BRANCH" ]; then
    printf '%s\n' "$INITIAL_BRANCH"
  fi
}

git_status_excluding_runtime() {
  git status --short | while IFS= read -r status_line || [ -n "$status_line" ]; do
    local path="${status_line#???}"
    case "$path" in
      .hoca-runtime|.hoca-runtime/*) continue ;;
    esac
    printf '%s\n' "$status_line"
  done
}

TARGET="$(target_branch)"
if [ -z "$TARGET" ]; then
  echo "Development branch restore: skipped (no dev or initial branch configured)"
  exit 0
fi

CURRENT="$(git branch --show-current 2>/dev/null || true)"
if [ -z "$CURRENT" ]; then
  echo "Development branch restore: skipped (detached HEAD)" >&2
  exit 0
fi

if [ "$CURRENT" = "$TARGET" ]; then
  echo "Development branch restore: already on $TARGET"
  exit 0
fi

DIRTY="$(git_status_excluding_runtime)"
if [ -n "$DIRTY" ]; then
  echo "Development branch restore: skipped (uncommitted changes on $CURRENT)"
  printf '%s\n' "$DIRTY"
  exit 0
fi

if ! git rev-parse --verify "$TARGET" >/dev/null 2>&1; then
  echo "Development branch restore: skipped (branch not found: $TARGET)" >&2
  exit 0
fi

if [ "$DRY_RUN" = "true" ]; then
  echo "Development branch restore: dry-run would switch $CURRENT -> $TARGET"
  exit 0
fi

echo "Restoring development branch: $TARGET (from $CURRENT)"
if ! git checkout "$TARGET"; then
  echo "Unable to restore development branch: $TARGET" >&2
  exit 1
fi
echo "Development branch restore: complete ($TARGET)"
