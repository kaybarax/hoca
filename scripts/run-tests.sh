#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: run-tests.sh /path/to/project /path/to/run-dir"
  exit 1
fi

PROJECT_PATH="$(cd "$1" && pwd)"
RUN_DIR="$(mkdir -p "$2" && cd "$2" && pwd)"

cd "$PROJECT_PATH"

STDOUT_LOG="$RUN_DIR/tests-output.log"
STDERR_LOG="$RUN_DIR/tests-stderr.log"
EXIT_CODE_FILE="$RUN_DIR/tests-exit-code.txt"
SUMMARY_FILE="$RUN_DIR/tests-summary.md"

: > "$STDOUT_LOG"
: > "$STDERR_LOG"
: > "$SUMMARY_FILE"

TESTS_RUN=0
OVERALL_EXIT=0
TEST_COMMAND=""
FAILURE_TYPE=""

run_test_command() {
  local name="$1"
  shift
  TEST_COMMAND="$*"
  echo "Running: $*" | tee -a "$STDOUT_LOG"
  set +e
  "$@" >> "$STDOUT_LOG" 2>> "$STDERR_LOG"
  local exit_code=$?
  set -e
  echo "$name exit code: $exit_code" | tee -a "$STDOUT_LOG"
  if [ "$exit_code" -ne 0 ]; then
    OVERALL_EXIT="$exit_code"
  fi
  return "$exit_code"
}

classify_failure() {
  local stderr_content
  stderr_content="$(cat "$STDERR_LOG" 2>/dev/null || true)"
  local stdout_content
  stdout_content="$(cat "$STDOUT_LOG" 2>/dev/null || true)"
  local combined="$stderr_content $stdout_content"

  if echo "$combined" | grep -qiE "command not found|no such file or directory|module.*not found|cannot find module|ModuleNotFoundError|ImportError|not installed|missing dependency|ENOENT|connection refused|timeout|permission denied"; then
    FAILURE_TYPE="environment"
    return
  fi

  if git diff --quiet HEAD~ -- . 2>/dev/null; then
    FAILURE_TYPE="pre-existing"
  else
    FAILURE_TYPE="current-task"
  fi
}

write_summary() {
  local status="$1"
  {
    echo "# Test Summary"
    echo ""
    echo "- **Status**: $status"
    echo "- **Exit code**: $OVERALL_EXIT"
    if [ -n "$TEST_COMMAND" ]; then
      echo "- **Command**: \`$TEST_COMMAND\`"
    fi
    if [ -n "$FAILURE_TYPE" ]; then
      echo "- **Failure type**: $FAILURE_TYPE"
    fi
    echo "- **Project**: $PROJECT_PATH"
    echo "- **Timestamp**: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$SUMMARY_FILE"
}

pick_node_runner() {
  if [ -f "pnpm-lock.yaml" ] && command -v pnpm >/dev/null 2>&1; then
    echo "pnpm"
  elif [ -f "yarn.lock" ] && command -v yarn >/dev/null 2>&1; then
    echo "yarn"
  else
    echo "npm"
  fi
}

if [ -f "package.json" ]; then
  if command -v jq >/dev/null 2>&1; then
    runner="$(pick_node_runner)"
    if jq -e '.scripts.test' package.json >/dev/null 2>&1; then
      TESTS_RUN=1
      if [ "$runner" = "npm" ]; then
        run_test_command "$runner test" "$runner" test || true
      else
        run_test_command "$runner test" "$runner" test || true
      fi
    fi
    if jq -e '.scripts.lint' package.json >/dev/null 2>&1; then
      TESTS_RUN=1
      if [ "$runner" = "npm" ]; then
        run_test_command "$runner lint" "$runner" run lint || true
      else
        run_test_command "$runner lint" "$runner" lint || true
      fi
    fi
    if jq -e '.scripts.typecheck' package.json >/dev/null 2>&1; then
      TESTS_RUN=1
      if [ "$runner" = "npm" ]; then
        run_test_command "$runner typecheck" "$runner" run typecheck || true
      else
        run_test_command "$runner typecheck" "$runner" typecheck || true
      fi
    fi
  fi
fi

if [ -f "pyproject.toml" ] || [ -f "requirements.txt" ]; then
  if command -v pytest >/dev/null 2>&1; then
    TESTS_RUN=1
    run_test_command "pytest" pytest || true
  fi
fi

if [ -f "go.mod" ]; then
  TESTS_RUN=1
  run_test_command "go test" go test ./... || true
fi

if [ -f "Cargo.toml" ]; then
  TESTS_RUN=1
  run_test_command "cargo test" cargo test || true
fi

if [ -f "Makefile" ] && grep -qE "^test:" Makefile; then
  TESTS_RUN=1
  run_test_command "make test" make test || true
fi

if [ "$TESTS_RUN" -eq 0 ]; then
  echo "No automated tests detected." | tee -a "$STDOUT_LOG"
  echo "0" > "$EXIT_CODE_FILE"
  write_summary "no-tests-detected"
  echo "Test phase complete."
  exit 0
fi

echo "$OVERALL_EXIT" > "$EXIT_CODE_FILE"

if [ "$OVERALL_EXIT" -ne 0 ]; then
  classify_failure
  write_summary "failed"
  echo "Tests failed (exit $OVERALL_EXIT, classified as $FAILURE_TYPE)."
  exit 1
fi

write_summary "passed"
echo "Test phase complete."
