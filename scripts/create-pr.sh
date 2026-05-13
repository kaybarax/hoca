#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: create-pr.sh /path/to/project \"task\" /path/to/run-dir [--issue-id ID]" >&2
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
TASK="$2"
RUN_DIR="$(mkdir -p "$3" && cd "$3" && pwd)"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_FILE="$PROJECT_PATH/templates/PR_TEMPLATE.md"

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH" >&2
  exit 1
fi

if [ ! -f "$TEMPLATE_FILE" ]; then
  echo "Missing PR template: $TEMPLATE_FILE" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is not installed or not on PATH." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run: gh auth login" >&2
  exit 1
fi

if git symbolic-ref -q refs/remotes/origin/HEAD >/dev/null 2>&1; then
  DEFAULT_BRANCH="$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')"
else
  DEFAULT_BRANCH="main"
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [ -z "$CURRENT_BRANCH" ]; then
  echo "Detached HEAD; create PRs from a named branch." >&2
  exit 1
fi

if [ "$CURRENT_BRANCH" = "$DEFAULT_BRANCH" ]; then
  echo "Refusing to open a PR from the default branch ($DEFAULT_BRANCH)." >&2
  exit 1
fi

TASK_ONELINE="$(printf '%s' "$TASK" | tr '\n\r' '  ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/[[:space:]]\{2,\}/ /g')"
if [ -z "$TASK_ONELINE" ]; then
  echo "Task text is empty; cannot build PR title or summary." >&2
  exit 1
fi

if printf '%s' "$TASK_ONELINE" | grep -qiE \
  '(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer[[:space:]]+[a-z0-9_-]{10,}|password[[:space:]]*=[[:space:]]*[^[:space:]]|-----BEGIN[[:space:]]+(RSA|OPENSSH|EC)[[:space:]]+PRIVATE[[:space:]]+KEY-----)'; then
  echo "Task text looks like it may contain secrets; refusing to build PR metadata automatically." >&2
  exit 1
fi

LOWER_TASK="$(printf '%s' "$TASK_ONELINE" | tr '[:upper:]' '[:lower:]')"
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

PR_TITLE="${CONVENTIONAL_PREFIX}: ${DESC}"
if [ -n "$ISSUE_ID" ]; then
  PR_TITLE="${PR_TITLE} (#${ISSUE_ID})"
fi

MAX_TITLE_LEN=120
if [ "${#PR_TITLE}" -gt "$MAX_TITLE_LEN" ]; then
  TRUNC_LEN=$((MAX_TITLE_LEN - 3))
  PR_TITLE="${PR_TITLE:0:$TRUNC_LEN}..."
fi

REMOTE_NAME="origin"
if ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  echo "Git remote '$REMOTE_NAME' is not configured." >&2
  exit 1
fi

echo "Pushing branch $CURRENT_BRANCH to $REMOTE_NAME..."
if ! git push -u "$REMOTE_NAME" HEAD; then
  echo "git push failed; cannot create PR." >&2
  exit 1
fi

write_fragment() {
  local name="$1"
  shift
  local f="$RUN_DIR/pr-fragment-${name}.txt"
  : > "$f"
  while [ "$#" -gt 0 ]; do
    printf '%s\n' "$1" >> "$f"
    shift
  done
}

write_fragment "summary" "$TASK_ONELINE"

if git rev-parse --verify "origin/${DEFAULT_BRANCH}" >/dev/null 2>&1; then
  BASE_REF="origin/${DEFAULT_BRANCH}"
elif git rev-parse --verify "${DEFAULT_BRANCH}" >/dev/null 2>&1; then
  BASE_REF="${DEFAULT_BRANCH}"
else
  BASE_REF=""
fi

{
  echo '```text'
  if [ -n "$BASE_REF" ]; then
    echo "Commits on this branch (not on ${BASE_REF}):"
    git log "${BASE_REF}..HEAD" --oneline 2>/dev/null || echo "(git log unavailable)"
    echo ""
    echo "Diff stat vs ${BASE_REF}:"
    git diff --stat "${BASE_REF}...HEAD" 2>/dev/null || echo "(git diff --stat unavailable)"
  else
    echo "Could not resolve base ref ${DEFAULT_BRANCH}; showing recent commits:"
    git log -15 --oneline
  fi
  echo '```'
} > "$RUN_DIR/pr-fragment-changes.txt"

if [ -f "$RUN_DIR/tests-summary.md" ]; then
  cp "$RUN_DIR/tests-summary.md" "$RUN_DIR/pr-fragment-validation.txt"
else
  write_fragment "validation" "_No \`tests-summary.md\` found in the run directory._"
fi

if [ -f "$RUN_DIR/aider-review.txt" ]; then
  {
    if grep -q "LGTM" "$RUN_DIR/aider-review.txt"; then
      echo "**Status**: LGTM present in Aider review output."
    else
      echo "**Status**: LGTM not detected in Aider review output (human review recommended)."
    fi
    echo ""
    echo "**Excerpt** (first 80 lines):"
    echo '```text'
    head -n 80 "$RUN_DIR/aider-review.txt"
    echo '```'
  } > "$RUN_DIR/pr-fragment-aider-review.txt"
else
  write_fragment "aider-review" "_No \`aider-review.txt\` found in the run directory._"
fi

if [ -f "$RUN_DIR/risk-notes.txt" ]; then
  cp "$RUN_DIR/risk-notes.txt" "$RUN_DIR/pr-fragment-risk.txt"
else
  write_fragment "risk" "None noted in run metadata."
fi

if [ -n "$ISSUE_ID" ]; then
  write_fragment "linked-issue" "Issue #${ISSUE_ID}"
else
  write_fragment "linked-issue" "None"
fi

slug_heading() {
  printf '%s' "$1" | sed 's/^## //' | tr '[:upper:]' '[:lower:]' | tr ' ' '-'
}

PR_BODY_FILE="$RUN_DIR/pr-body.md"
{
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^##[[:space:]] ]]; then
      key="$(slug_heading "$line")"
      echo "$line"
      echo ""
      frag="$RUN_DIR/pr-fragment-${key}.txt"
      if [ -f "$frag" ]; then
        cat "$frag"
      else
        echo "_No automated content for this section._"
      fi
      echo ""
    else
      echo "$line"
    fi
  done < "$TEMPLATE_FILE"
  echo "**Auto-merge**: disabled (default). This pull request will not be merged automatically."
  echo ""
  if [ -n "$ISSUE_ID" ]; then
    echo "Refs: #${ISSUE_ID}"
    echo ""
  fi
} > "$PR_BODY_FILE"

echo "Creating pull request (base: ${DEFAULT_BRANCH})..."
set +e
GH_OUT="$(gh pr create --title "$PR_TITLE" --body-file "$PR_BODY_FILE" --base "$DEFAULT_BRANCH" 2>&1)"
GH_EC=$?
set -e
printf '%s\n' "$GH_OUT" | tee "$RUN_DIR/gh-pr-create.log"
if [ "$GH_EC" -ne 0 ]; then
  echo "gh pr create failed (exit $GH_EC)." >&2
  exit 1
fi

PR_URL=""
if command -v jq >/dev/null 2>&1; then
  PR_URL="$(gh pr view --json url -q .url 2>/dev/null || true)"
fi
if [ -z "$PR_URL" ]; then
  PR_URL="$(printf '%s\n' "$GH_OUT" | grep -Eo 'https://[^[:space:]]+/pull/[0-9]+' | tail -n 1 || true)"
fi
if [ -z "$PR_URL" ]; then
  echo "PR was created but URL could not be parsed; check gh-pr-create.log and run: gh pr view --json url" >&2
  exit 1
fi

printf '%s\n' "$PR_URL" > "$RUN_DIR/pr-url.txt"
echo "PR URL saved to $RUN_DIR/pr-url.txt"

ENGINEER_NOTIFY="$RUN_DIR/engineer-followup.txt"
{
  echo "Human engineer follow-up (HOCA merge policy 18.1)"
  echo ""
  echo "A pull request was created and is left open for your review."
  echo "HOCA does not merge pull requests automatically in the default configuration."
  echo "HOCA does not delete the remote branch from this step; remove it only after a successful merge if desired."
  echo ""
  echo "Pull request: $PR_URL"
} > "$ENGINEER_NOTIFY"

echo "" >&2
echo "================================================================" >&2
echo "  Human engineer: pull request is open for review." >&2
echo "  Auto-merge: off (default). This PR will not be merged by HOCA." >&2
echo "  Remote branch: retained (no automatic delete on open or failed merge)." >&2
echo "  Written: $ENGINEER_NOTIFY" >&2
echo "================================================================" >&2

echo "$PR_URL"
