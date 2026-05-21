#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="${TMPDIR:-/tmp}"
TMP_ROOT="${TMP_ROOT%/}"
WORK_DIR="$(mktemp -d "$TMP_ROOT/hoca-acceptance.XXXXXX")"
TEST_REPO="$WORK_DIR/disposable-repo"
ISSUE_REPO="$WORK_DIR/disposable-issue-repo"
TASK="Add a Local Development section to README.md. Keep it short and clear."
ISSUE_TITLE="Add a short issue note to README.md"
USE_FAKE_TOOLS="${HOCA_ACCEPTANCE_FAKE_TOOLS:-false}"

print_step() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf 'Acceptance test failed: %s\n' "$1" >&2
  printf 'Disposable repo: %s\n' "$TEST_REPO" >&2
  printf 'Work directory: %s\n' "$WORK_DIR" >&2
  exit 1
}

assert_file_exists() {
  local path="$1"
  local label="$2"
  [ -e "$path" ] || fail "$label not found: $path"
}

assert_file_contains() {
  local path="$1"
  local pattern="$2"
  local label="$3"
  grep -qE "$pattern" "$path" || fail "$label did not contain expected pattern: $pattern"
}

latest_run_dir() {
  local repo="$1"
  find "$repo/.hoca-runtime/runs" -mindepth 1 -maxdepth 1 -type d \
    | sort \
    | tail -n 1
}

create_disposable_repo() {
  local repo="$1"
  local remote="$repo-origin.git"
  mkdir -p "$repo"
  git -C "$repo" init >/dev/null
  git -C "$repo" config user.email "hoca-acceptance@example.test"
  git -C "$repo" config user.name "HOCA Acceptance"
  printf '# Disposable Acceptance Repo\n\nSmall repo for HOCA acceptance testing.\n' > "$repo/README.md"
  git -C "$repo" add -- README.md
  git -C "$repo" commit -m "Initial README" >/dev/null
  git init --bare "$remote" >/dev/null
  git -C "$repo" remote add origin "$remote"
  git -C "$repo" push -u origin HEAD >/dev/null
}

add_python_shim() {
  local python_bin="${HOCA_ACCEPTANCE_PYTHON:-}"
  if [ -z "$python_bin" ] && [ -x "$HOCA_ROOT/.venv/bin/python" ]; then
    python_bin="$HOCA_ROOT/.venv/bin/python"
  fi

  [ -n "$python_bin" ] || return 0

  local shim_bin="$WORK_DIR/python-shim"
  mkdir -p "$shim_bin"
  cat > "$shim_bin/python3" <<EOS
#!/usr/bin/env bash
exec "$python_bin" "\$@"
EOS
  chmod +x "$shim_bin/python3"
  PATH="$shim_bin:$PATH"
  export PATH
}

make_fake_tools() {
  local fake_bin="$WORK_DIR/fake-bin"
  mkdir -p "$fake_bin"

  cat > "$fake_bin/gh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
  exit 0
fi
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "create" ]; then
  printf 'https://github.com/example/disposable/pull/1\n'
  exit 0
fi
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  printf 'https://github.com/example/disposable/pull/1\n'
  exit 0
fi
if [ "${1:-}" = "repo" ] && [ "${2:-}" = "view" ]; then
  printf 'example/disposable\n'
  exit 0
fi
if [ "${1:-}" = "api" ]; then
  printf 'false\n'
  exit 0
fi
exit 0
EOS

  cat > "$fake_bin/node" <<'EOS'
#!/usr/bin/env bash
printf 'v20.0.0\n'
EOS

  cat > "$fake_bin/docker" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "info" ]; then
  exit 0
fi
if [ "${1:-}" = "image" ] && [ "${2:-}" = "inspect" ]; then
  if [ "${3:-}" = "--format" ]; then
    printf 'worker\n'
  else
    printf '[]\n'
  fi
  exit 0
fi
exit 0
EOS

  cat > "$fake_bin/curl" <<'EOS'
#!/usr/bin/env bash
exit 0
EOS

  cat > "$fake_bin/ollama" <<'EOS'
#!/usr/bin/env bash
cat <<'EOF'
NAME ID SIZE MODIFIED
qwen-7b-pro abc 1GB now
EOF
EOS

  cat > "$fake_bin/openhands" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--help" ]; then
  printf 'openhands --headless --task --override-with-envs --json\n'
  exit 0
fi
if [ "${HOCA_AGENT_ROLE:-}" = "reviewer" ]; then
  printf 'Structured review artifact is authoritative.\nLGTM\n'
  exit 0
fi
RUN_DIR="$(find .hoca-runtime/runs -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
{
  printf '\n## Local Development\n\n'
  printf 'Run tests with your project command before opening a PR.\n'
} >> README.md
printf 'README.md\n' > "$RUN_DIR/intended-files.txt"
printf 'manager\n' > "$RUN_DIR/intended-files-source.txt"
printf 'low\n' > "$RUN_DIR/risk-level.txt"
printf 'OpenHands fake run attempted README update.\n'
EOS

  chmod +x "$fake_bin/gh" "$fake_bin/node" "$fake_bin/docker" "$fake_bin/curl"
  chmod +x "$fake_bin/ollama" "$fake_bin/openhands"
  PATH="$fake_bin:$PATH"
  export PATH
}

add_python_shim

if [ "$USE_FAKE_TOOLS" = "true" ]; then
  print_step "Installing fake acceptance tools"
  export HOCA_USE_SANDBOX=false
  export HOCA_USE_WORKTREE_SANDBOX=false
  export HOCA_KEEP_RUNTIME=true
  export HOCA_RESTORE_DEV_BRANCH=false
  make_fake_tools
fi

print_step "Recording HOCA workspace baseline"
HOCA_STATUS_BEFORE="$(git -C "$HOCA_ROOT" status --short --untracked-files=all)"

print_step "Running hoca doctor"
"$HOCA_ROOT/bin/hoca" doctor

print_step "Creating disposable Git repository"
create_disposable_repo "$TEST_REPO"
START_BRANCH="$(git -C "$TEST_REPO" branch --show-current)"

print_step "Running init-project"
"$SCRIPT_DIR/init-project.sh" "$TEST_REPO"
assert_file_exists "$TEST_REPO/.openhands_instructions" "OpenHands instructions template"
assert_file_exists "$TEST_REPO/.hoca-runtime/runs" "HOCA runtime runs directory"
assert_file_contains "$TEST_REPO/.gitignore" '^\.hoca-runtime/$' ".gitignore"

git -C "$TEST_REPO" add -- .gitignore .openhands_instructions templates/PR_TEMPLATE.md
git -C "$TEST_REPO" commit -m "Initialize HOCA project config" >/dev/null

print_step "Running hoca run"
set +e
"$HOCA_ROOT/bin/hoca" run "$TEST_REPO" "$TASK" > "$WORK_DIR/hoca-run-output.log" 2> "$WORK_DIR/hoca-run-stderr.log"
RUN_EXIT=$?
set -e

cat "$WORK_DIR/hoca-run-output.log"
if [ -s "$WORK_DIR/hoca-run-stderr.log" ]; then
  cat "$WORK_DIR/hoca-run-stderr.log" >&2
fi

if [ "$RUN_EXIT" -ne 0 ]; then
  fail "bin/hoca run exited with $RUN_EXIT"
fi

RUN_DIR="$(latest_run_dir "$TEST_REPO")"
[ -n "$RUN_DIR" ] || fail "no HOCA run directory was created"

print_step "Verifying branch and run artifacts"
CURRENT_BRANCH="$(git -C "$TEST_REPO" branch --show-current)"
if [ "$CURRENT_BRANCH" = "$START_BRANCH" ]; then
  fail "HOCA did not create or switch to a task branch"
fi
case "$CURRENT_BRANCH" in
  feat/*|fix/*) ;;
  *) fail "unexpected task branch name: $CURRENT_BRANCH" ;;
esac

assert_file_exists "$RUN_DIR/status.json" "run status"
assert_file_exists "$RUN_DIR/workspace-validation.txt" "workspace validation log"
assert_file_exists "$RUN_DIR/openhands-exit-code.txt" "OpenHands exit code"
assert_file_exists "$RUN_DIR/tests-summary.md" "test summary"
assert_file_exists "$RUN_DIR/openhands-review.txt" "Code review"
assert_file_exists "$RUN_DIR/commit-hash.txt" "commit hash"
assert_file_exists "$RUN_DIR/pr-url.txt" "pull request URL"
assert_file_contains "$RUN_DIR/openhands-exit-code.txt" '^0$' "OpenHands exit code"
assert_file_contains "$RUN_DIR/tests-summary.md" 'no-tests-detected' "no-test project summary"
assert_file_contains "$RUN_DIR/openhands-review.txt" 'LGTM' "Code review"
assert_file_exists "$RUN_DIR/reviews/review-report-1.json" "structured review report"
assert_file_contains "$RUN_DIR/reviews/review-report-1.json" '"verdict": "LGTM"' "structured review verdict"
assert_file_contains "$RUN_DIR/pr-url.txt" '^https://github.com/example/disposable/pull/1$' "pull request URL"
assert_file_contains "$TEST_REPO/README.md" 'Local Development' "README update"

if [ -f "$RUN_DIR/openhands-output.jsonl" ]; then
  assert_file_contains "$RUN_DIR/openhands-output.jsonl" 'README' "OpenHands output"
elif [ -f "$RUN_DIR/openhands-output.log" ]; then
  assert_file_contains "$RUN_DIR/openhands-output.log" 'README' "OpenHands output"
else
  fail "OpenHands output log was not created"
fi

print_step "Verifying staging and safety boundaries"
STAGED_FILES="$(git -C "$TEST_REPO" diff --cached --name-only)"
if [ -n "$STAGED_FILES" ]; then
  printf '%s\n' "$STAGED_FILES" >&2
  fail "files remained staged after commit"
fi

if find "$TEST_REPO" \
  \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.kubeconfig' \) \
  -print -quit | grep -q .; then
  fail "secret-like file was created in disposable repo"
fi

print_step "Creating disposable issue repository"
create_disposable_repo "$ISSUE_REPO"
ISSUE_START_BRANCH="$(git -C "$ISSUE_REPO" branch --show-current)"

print_step "Running init-project for issue repository"
"$SCRIPT_DIR/init-project.sh" "$ISSUE_REPO"
git -C "$ISSUE_REPO" add -- .gitignore .openhands_instructions templates/PR_TEMPLATE.md
git -C "$ISSUE_REPO" commit -m "Initialize HOCA project config" >/dev/null

print_step "Running hoca issue"
set +e
"$HOCA_ROOT/bin/hoca" issue "$ISSUE_REPO" 123 "$ISSUE_TITLE" > "$WORK_DIR/hoca-issue-output.log" 2> "$WORK_DIR/hoca-issue-stderr.log"
ISSUE_EXIT=$?
set -e

cat "$WORK_DIR/hoca-issue-output.log"
if [ -s "$WORK_DIR/hoca-issue-stderr.log" ]; then
  cat "$WORK_DIR/hoca-issue-stderr.log" >&2
fi

if [ "$ISSUE_EXIT" -ne 0 ]; then
  fail "bin/hoca issue exited with $ISSUE_EXIT"
fi

ISSUE_RUN_DIR="$ISSUE_REPO/.hoca-runtime/runs/issue-123"
assert_file_exists "$ISSUE_RUN_DIR/status.json" "issue run status"
assert_file_contains "$ISSUE_RUN_DIR/status.json" '"issue_id": "123"' "issue run status"
assert_file_contains "$ISSUE_RUN_DIR/status.json" 'Fix GitHub issue #123' "issue run task"
assert_file_contains "$ISSUE_RUN_DIR/openhands-exit-code.txt" '^0$' "issue OpenHands exit code"
assert_file_exists "$ISSUE_RUN_DIR/commit-hash.txt" "issue commit hash"
assert_file_exists "$ISSUE_RUN_DIR/pr-url.txt" "issue pull request URL"
assert_file_contains "$ISSUE_REPO/README.md" 'Local Development' "issue README update"

ISSUE_BRANCH="$(git -C "$ISSUE_REPO" branch --show-current)"
if [ "$ISSUE_BRANCH" = "$ISSUE_START_BRANCH" ]; then
  fail "HOCA issue did not create or switch to an issue branch"
fi
if [ "$ISSUE_BRANCH" != "fix/issue-123" ]; then
  fail "unexpected issue branch name: $ISSUE_BRANCH"
fi

HOCA_STATUS_AFTER="$(git -C "$HOCA_ROOT" status --short --untracked-files=all)"
if [ "$HOCA_STATUS_AFTER" != "$HOCA_STATUS_BEFORE" ]; then
  printf 'Before:\n%s\nAfter:\n%s\n' "$HOCA_STATUS_BEFORE" "$HOCA_STATUS_AFTER" >&2
  fail "files outside the disposable repo changed in the HOCA workspace"
fi

print_step "Acceptance test passed"
printf 'Disposable repo: %s\n' "$TEST_REPO"
printf 'Run directory: %s\n' "$RUN_DIR"
printf 'Work directory: %s\n' "$WORK_DIR"
