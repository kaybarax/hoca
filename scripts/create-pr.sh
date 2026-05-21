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
TARGET_TEMPLATE_FILE="$PROJECT_PATH/templates/PR_TEMPLATE.md"
HOCA_TEMPLATE_FILE="$(cd "$SCRIPT_DIR/.." && pwd)/templates/PR_TEMPLATE.md"
TEMPLATE_FILE="$TARGET_TEMPLATE_FILE"

cd "$PROJECT_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a Git repository: $PROJECT_PATH" >&2
  exit 1
fi

if [ ! -f "$TEMPLATE_FILE" ]; then
  if [ -f "$HOCA_TEMPLATE_FILE" ]; then
    TEMPLATE_FILE="$HOCA_TEMPLATE_FILE"
    echo "Using bundled HOCA PR template: $TEMPLATE_FILE"
  else
    TEMPLATE_FILE="$RUN_DIR/pr-template-fallback.md"
    cat > "$TEMPLATE_FILE" <<'EOF'
## Summary

## Changes

## Validation

## Code Review

## Risk

## Linked Issue
EOF
    echo "Using generated fallback PR template: $TEMPLATE_FILE"
  fi
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is not installed or not on PATH." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Run: gh auth login" >&2
  exit 1
fi

AUTO_MERGE_PRECHECK_RC=2
GUARDS_SH="$SCRIPT_DIR/auto-merge-guards.sh"
if [ -f "$RUN_DIR/status.json" ] && [ -f "$GUARDS_SH" ]; then
  if bash "$GUARDS_SH" wants-auto-merge "$RUN_DIR"; then
    if bash "$GUARDS_SH" precheck "$RUN_DIR"; then
      AUTO_MERGE_PRECHECK_RC=0
    else
      AUTO_MERGE_PRECHECK_RC=1
    fi
  fi
fi

AUTO_MERGE_FOOTER=""
if [ "$AUTO_MERGE_PRECHECK_RC" -eq 0 ]; then
  AUTO_MERGE_FOOTER="**Auto-merge**: enabled by HOCA (local prechecks passed). This script runs \`gh pr merge --auto --merge --delete-branch\` after the PR is created so GitHub merges when branch protections and checks allow."
elif [ "$AUTO_MERGE_PRECHECK_RC" -eq 1 ]; then
  AUTO_MERGE_FOOTER="**Auto-merge**: requested in \`status.json\` but local prechecks failed — see \`auto-merge-precheck-skip.txt\` in the run directory. This PR will not be auto-merged by HOCA."
else
  AUTO_MERGE_FOOTER="**Auto-merge**: disabled (default). This pull request will not be merged automatically."
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

if git rev-parse --verify "origin/${DEFAULT_BRANCH}" >/dev/null 2>&1; then
  BASE_REF="origin/${DEFAULT_BRANCH}"
elif git rev-parse --verify "${DEFAULT_BRANCH}" >/dev/null 2>&1; then
  BASE_REF="${DEFAULT_BRANCH}"
else
  BASE_REF=""
fi

CHANGES_FILE="$RUN_DIR/pr-fragment-changes.txt"
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
} > "$CHANGES_FILE"

HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PR_FRAGMENT_ARGS=(
  --task "$TASK_ONELINE"
  --changes-file "$CHANGES_FILE"
)
if [ -n "$ISSUE_ID" ]; then
  PR_FRAGMENT_ARGS+=(--issue-id "$ISSUE_ID")
fi
if ! PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.pr_body "$RUN_DIR" \
  "${PR_FRAGMENT_ARGS[@]}" >/dev/null; then
  echo "Failed to build PR body fragments from run artifacts." >&2
  exit 1
fi

DRAFT_PR_FLAG=""
if [ -f "$RUN_DIR/draft-pr-with-blockers.flag" ]; then
  DRAFT_PR_FLAG="--draft"
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
  printf '%s\n' "$AUTO_MERGE_FOOTER"
  echo ""
  if [ -n "$ISSUE_ID" ]; then
    echo "Refs: #${ISSUE_ID}"
    echo ""
  fi
} > "$PR_BODY_FILE"

echo "Creating pull request (base: ${DEFAULT_BRANCH})..."
set +e
GH_OUT="$(gh pr create --title "$PR_TITLE" --body-file "$PR_BODY_FILE" --base "$DEFAULT_BRANCH" $DRAFT_PR_FLAG 2>&1)"
GH_EC=$?
set -e
printf '%s\n' "$GH_OUT" | tee "$RUN_DIR/gh-pr-create.log"
if [ "$GH_EC" -ne 0 ]; then
  echo "gh pr create failed (exit $GH_EC)." >&2
  if command -v jq >/dev/null 2>&1 && [ -f "$RUN_DIR/status.json" ]; then
    jq --arg reason "pr_creation_failed" '.status = "failed" | .reason = $reason' \
      "$RUN_DIR/status.json" > "$RUN_DIR/status.tmp"
    mv "$RUN_DIR/status.tmp" "$RUN_DIR/status.json"
  fi
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
if [ -f "$RUN_DIR/status.json" ] && command -v jq >/dev/null 2>&1; then
  jq --arg url "$PR_URL" '.pr_url = $url' "$RUN_DIR/status.json" > "$RUN_DIR/status.tmp"
  mv "$RUN_DIR/status.tmp" "$RUN_DIR/status.json"
fi
if PYTHONPATH="$(cd "$SCRIPT_DIR/.." && pwd)${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m hoca.run_artifacts sync-status "$RUN_DIR" >/dev/null 2>&1; then
  :
fi

MERGE_OUTCOME="open_for_review"
if [ "$AUTO_MERGE_PRECHECK_RC" -eq 0 ]; then
  if bash "$GUARDS_SH" postcheck-mergeable; then
    set +e
    GH_MERGE_LOG="$RUN_DIR/gh-pr-merge.log"
    gh pr merge --auto --merge --delete-branch >"$GH_MERGE_LOG" 2>&1
    MERGE_EC=$?
    set -e
    if [ "$MERGE_EC" -eq 0 ]; then
      MERGE_OUTCOME="auto_merge_enabled"
      {
        echo "HOCA auto-merge (milestone 18.2)"
        echo ""
        echo "Queued GitHub auto-merge with branch delete after merge:"
        echo "  gh pr merge --auto --merge --delete-branch"
        echo ""
        echo "Pull request: $PR_URL"
      } > "$RUN_DIR/auto-merge-outcome.txt"
      if command -v jq >/dev/null 2>&1 && [ -f "$RUN_DIR/status.json" ]; then
        jq '.merge_performed = false | .auto_merge_queued = true | .merge_command = "gh pr merge --auto --merge --delete-branch"' \
          "$RUN_DIR/status.json" > "$RUN_DIR/status.tmp"
        mv "$RUN_DIR/status.tmp" "$RUN_DIR/status.json"
      fi
    else
      MERGE_OUTCOME="auto_merge_gh_failed"
      {
        echo "HOCA attempted auto-merge but gh pr merge failed (exit $MERGE_EC)."
        echo "See: $GH_MERGE_LOG"
      } > "$RUN_DIR/gh-pr-merge-error.txt"
    fi
  else
    MERGE_OUTCOME="not_mergeable_on_github"
    {
      echo "HOCA auto-merge prechecks passed, but the pull request is not MERGEABLE on GitHub after waiting (conflicts or unresolved merge state)."
      echo "See: $PR_URL"
    } > "$RUN_DIR/auto-merge-postcheck-skip.txt"
  fi
fi

ENGINEER_NOTIFY="$RUN_DIR/engineer-followup.txt"
if [ "$MERGE_OUTCOME" = "auto_merge_enabled" ]; then
  {
    echo "HOCA merge follow-up (milestone 18.2 — auto-merge queued)"
    echo ""
    echo "Local safety checks passed and GitHub auto-merge was requested for this pull request."
    echo "The branch will be deleted by GitHub after a successful merge if your repository settings allow."
    echo ""
    echo "Pull request: $PR_URL"
  } > "$ENGINEER_NOTIFY"
else
  {
    echo "Human engineer follow-up (HOCA merge policy)"
    echo ""
    echo "A pull request was created."
    if [ "$AUTO_MERGE_PRECHECK_RC" -eq 0 ] && [ "$MERGE_OUTCOME" != "auto_merge_enabled" ]; then
      echo "Auto-merge was requested but could not be completed automatically; see files in the run directory."
    else
      echo "Review and merge when ready (or adjust run metadata and re-open a PR if appropriate)."
    fi
    echo "HOCA does not delete the remote branch except when GitHub completes an auto-merge with --delete-branch."
    echo ""
    echo "Pull request: $PR_URL"
  } > "$ENGINEER_NOTIFY"
fi

if [ -x "$SCRIPT_DIR/generate-task-report.sh" ]; then
  "$SCRIPT_DIR/generate-task-report.sh" "$PROJECT_PATH" "$RUN_DIR" >/dev/null || true
fi

echo "" >&2
echo "================================================================" >&2
if [ "$MERGE_OUTCOME" = "auto_merge_enabled" ]; then
  echo "  GitHub auto-merge was queued for this PR (strict rules satisfied)." >&2
elif [ "$AUTO_MERGE_PRECHECK_RC" -eq 0 ]; then
  echo "  Auto-merge was requested but not queued; see run directory logs." >&2
else
  echo "  Human engineer: pull request is open for review." >&2
  echo "  Auto-merge: off or prechecks did not pass." >&2
fi
echo "  Written: $ENGINEER_NOTIFY" >&2
echo "================================================================" >&2

echo "$PR_URL"
