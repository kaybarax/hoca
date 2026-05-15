#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="${TMPDIR:-/tmp}"
TMP_ROOT="${TMP_ROOT%/}"
WORK_DIR="$(mktemp -d "$TMP_ROOT/hoca-acceptance.XXXXXX")"
TEST_REPO="$WORK_DIR/disposable-repo"
TASK="Add a Local Development section to README.md. Keep it short and clear."
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
  find "$TEST_REPO/.hoca-runtime/runs" -mindepth 1 -maxdepth 1 -type d \
    | sort \
    | tail -n 1
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
{
  printf '\n## Local Development\n\n'
  printf 'Run tests with your project command before opening a PR.\n'
} >> README.md
printf 'OpenHands fake run attempted README update.\n'
EOS

  cat > "$fake_bin/aider" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then
  printf 'aider 1.0\n'
  exit 0
fi
printf 'Review complete.\n'
printf 'LGTM\n'
EOS

  chmod +x "$fake_bin/gh" "$fake_bin/node" "$fake_bin/docker" "$fake_bin/curl"
  chmod +x "$fake_bin/ollama" "$fake_bin/openhands" "$fake_bin/aider"
  PATH="$fake_bin:$PATH"
  export PATH
}

add_python_shim

if [ "$USE_FAKE_TOOLS" = "true" ]; then
  print_step "Installing fake acceptance tools"
  make_fake_tools
fi

print_step "Recording HOCA workspace baseline"
HOCA_STATUS_BEFORE="$(git -C "$HOCA_ROOT" status --short --untracked-files=all)"

print_step "Creating disposable Git repository"
mkdir -p "$TEST_REPO"
git -C "$TEST_REPO" init >/dev/null
git -C "$TEST_REPO" config user.email "hoca-acceptance@example.test"
git -C "$TEST_REPO" config user.name "HOCA Acceptance"
printf '# Disposable Acceptance Repo\n\nSmall repo for HOCA acceptance testing.\n' > "$TEST_REPO/README.md"
git -C "$TEST_REPO" add -- README.md
git -C "$TEST_REPO" commit -m "Initial README" >/dev/null
START_BRANCH="$(git -C "$TEST_REPO" branch --show-current)"

print_step "Running init-project"
"$SCRIPT_DIR/init-project.sh" "$TEST_REPO"
assert_file_exists "$TEST_REPO/.openhands_instructions" "OpenHands instructions template"
assert_file_exists "$TEST_REPO/.aider.conf.yml" "Aider config template"
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

RUN_DIR="$(latest_run_dir)"
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
assert_file_exists "$RUN_DIR/aider-review.txt" "Aider review"
assert_file_contains "$RUN_DIR/openhands-exit-code.txt" '^0$' "OpenHands exit code"
assert_file_contains "$RUN_DIR/tests-summary.md" 'no-tests-detected' "no-test project summary"
assert_file_contains "$RUN_DIR/aider-review.txt" 'LGTM' "Aider review"
assert_file_contains "$TEST_REPO/README.md" 'Local Development' "README update"

if [ -f "$RUN_DIR/openhands-output.jsonl" ]; then
  assert_file_contains "$RUN_DIR/openhands-output.jsonl" 'README' "OpenHands output"
elif [ -f "$RUN_DIR/openhands-output.log" ]; then
  assert_file_contains "$RUN_DIR/openhands-output.log" 'README' "OpenHands output"
else
  fail "OpenHands output log was not created"
fi

print_step "Verifying staging and safety boundaries"
if git -C "$TEST_REPO" diff --cached --quiet; then
  printf 'No files are staged, as expected without an intended-files.txt review artifact.\n'
else
  STAGED_FILES="$(git -C "$TEST_REPO" diff --cached --name-only)"
  if [ "$STAGED_FILES" != "README.md" ]; then
    printf '%s\n' "$STAGED_FILES" >&2
    fail "unexpected files were staged"
  fi
fi

if find "$TEST_REPO" \
  \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.kubeconfig' \) \
  -print -quit | grep -q .; then
  fail "secret-like file was created in disposable repo"
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
