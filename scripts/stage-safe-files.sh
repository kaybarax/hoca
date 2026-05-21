#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: stage-safe-files.sh /path/to/project /path/to/file-list" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
FILE_LIST="$2"

if [ ! -f "$FILE_LIST" ]; then
  echo "File list not found: $FILE_LIST" >&2
  exit 1
fi

cd "$PROJECT_PATH"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "Not a git repository: $PROJECT_PATH" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/hoca-security.sh
source "$SCRIPT_DIR/lib/hoca-security.sh"

UNSAFE=0

while IFS= read -r file || [ -n "$file" ]; do
  [ -z "$file" ] && continue

  if ! hoca_validate_staging_path "$REPO_ROOT" "$file"; then
    case "$file" in
      .hoca-runtime/*|.hoca-runtime)
        echo "Refusing to stage runtime file: $file" >&2
        ;;
      *)
        if hoca_path_is_secret_like "$file"; then
          echo "Refusing to stage secret-like file: $file" >&2
        else
          echo "Refusing to stage unsafe file: $file" >&2
        fi
        ;;
    esac
    UNSAFE=1
  fi
done < "$FILE_LIST"

if [ "$UNSAFE" -ne 0 ]; then
  echo "Aborting: unsafe files detected in file list." >&2
  exit 1
fi

while IFS= read -r file || [ -n "$file" ]; do
  [ -z "$file" ] && continue
  git add -- "$file"
done < "$FILE_LIST"

git diff --cached --check
git diff --cached
