#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: init-project.sh /path/to/target-repo"
  exit 1
fi

TARGET="$1"

if [ ! -d "$TARGET" ]; then
  echo "Target path does not exist: $TARGET"
  exit 1
fi

PROJECT_PATH="$(cd "$TARGET" && pwd)"
HOCA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Target path is not a Git repository: $PROJECT_PATH"
  exit 1
fi

echo "Initializing HOCA project config in: $PROJECT_PATH"

CREATED=()
EXISTED=()

copy_template() {
  local filename="$1"
  local src="$HOCA_ROOT/templates/$filename"
  if [ ! -f "$src" ]; then
    echo "Warning: template not found: $src"
    return
  fi
  if [ ! -f "$filename" ]; then
    cp "$src" "$filename"
    CREATED+=("$filename")
  else
    EXISTED+=("$filename")
  fi
}

copy_template ".openhands_instructions"

mkdir -p templates
if [ ! -f "templates/PR_TEMPLATE.md" ]; then
  if [ -f "$HOCA_ROOT/templates/PR_TEMPLATE.md" ]; then
    cp "$HOCA_ROOT/templates/PR_TEMPLATE.md" "templates/PR_TEMPLATE.md"
    CREATED+=("templates/PR_TEMPLATE.md")
  else
    echo "Warning: template not found: $HOCA_ROOT/templates/PR_TEMPLATE.md"
  fi
else
  EXISTED+=("templates/PR_TEMPLATE.md")
fi

mkdir -p .hoca-runtime/runs
mkdir -p .hoca-runtime/logs
CREATED+=(".hoca-runtime/runs/" ".hoca-runtime/logs/")

add_gitignore_rule() {
  local rule="$1"
  if [ ! -f .gitignore ]; then
    echo "$rule" > .gitignore
    return 0
  fi
  if ! grep -qxF "$rule" .gitignore; then
    if [ -s .gitignore ] && [ "$(tail -c 1 .gitignore)" != "" ]; then
      printf '\n' >> .gitignore
    fi
    echo "$rule" >> .gitignore
    return 0
  fi
  return 1
}

GITIGNORE_ADDED=()

if add_gitignore_rule ".hoca-runtime/"; then
  GITIGNORE_ADDED+=(".hoca-runtime/")
fi
if add_gitignore_rule ".openhands/"; then
  GITIGNORE_ADDED+=(".openhands/")
fi

echo ""
echo "--- Created ---"
if [ ${#CREATED[@]} -gt 0 ]; then
  for f in "${CREATED[@]}"; do
    echo "  $f"
  done
else
  echo "  (none)"
fi

echo ""
echo "--- Already existed ---"
if [ ${#EXISTED[@]} -gt 0 ]; then
  for f in "${EXISTED[@]}"; do
    echo "  $f"
  done
else
  echo "  (none)"
fi

if [ ${#GITIGNORE_ADDED[@]} -gt 0 ]; then
  echo ""
  echo "--- Added to .gitignore ---"
  for r in "${GITIGNORE_ADDED[@]}"; do
    echo "  $r"
  done
fi

echo ""
echo "HOCA project initialization complete."
echo ""
echo "Next steps:"
echo "  1. Review the created files and customize if needed."
echo "  2. Run 'hoca doctor' to verify your environment."
echo "  3. Run 'hoca run $PROJECT_PATH \"Your task\"' to start a task."
