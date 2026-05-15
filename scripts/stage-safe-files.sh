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

SECRET_PATTERNS=(
  '.env' '.env.*'
  '*.pem' '*.key' '*.p12' '*.pfx'
  'id_rsa' 'id_rsa.*' 'id_ed25519' 'id_ed25519.*'
  '*.kubeconfig'
  '*.keystore' '*.jks'
  '*credentials*'
  '*.secret' '*.secrets'
  '.netrc' '.npmrc' '.pypirc'
  '.htpasswd'
)

is_secret_like() {
  local filename
  filename="$(basename "$1")"
  local lower
  lower="$(printf '%s' "$filename" | tr '[:upper:]' '[:lower:]')"

  for pattern in "${SECRET_PATTERNS[@]}"; do
    # shellcheck disable=SC2254
    case "$lower" in
      $pattern) return 0 ;;
    esac
  done
  return 1
}

UNSAFE=0

while IFS= read -r file || [ -n "$file" ]; do
  [ -z "$file" ] && continue

  if [[ "$file" = /* ]]; then
    echo "Refusing absolute path: $file" >&2
    UNSAFE=1
    continue
  fi

  if [[ "$file" == *..* ]]; then
    echo "Refusing path traversal: $file" >&2
    UNSAFE=1
    continue
  fi

  resolved="$(cd "$REPO_ROOT" && python3 -c "import os,sys; print(os.path.normpath(os.path.join(sys.argv[1], sys.argv[2])))" "$REPO_ROOT" "$file")" || resolved=""
  if [ -z "$resolved" ] || [[ "$resolved" != "$REPO_ROOT"/* ]]; then
    echo "Refusing path outside repository: $file" >&2
    UNSAFE=1
    continue
  fi

  if is_secret_like "$file"; then
    echo "Refusing to stage secret-like file: $file" >&2
    UNSAFE=1
    continue
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
