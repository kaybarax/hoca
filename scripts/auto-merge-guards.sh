#!/usr/bin/env bash
# HOCA milestone 18.2: strict prechecks before enabling GitHub auto-merge.
# Usage:
#   auto-merge-guards.sh wants-auto-merge <run-dir>     -> exit 0 if yes, 1 if no
#   auto-merge-guards.sh precheck <run-dir>             -> exit 0 pass, 1 fail, 2 skip (not requested)
#   auto-merge-guards.sh postcheck-mergeable           -> exit 0 if MERGEABLE, 1 otherwise (reads PR from gh)
set -euo pipefail

SKIP_FILE=""
RUN_DIR=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log_skip() {
  printf '%s\n' "$1" >> "$SKIP_FILE"
}

path_is_secret_like() {
  local path="$1"
  case "$path" in
    .hoca-runtime/*|.hoca-runtime|.git/*|.git)
      return 0
      ;;
  esac
  local base lower
  base="$(basename "$path")"
  lower="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"
  case "$lower" in
    *.example|*.sample|*.template)
      return 1
      ;;
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa|id_rsa.*|id_ed25519|id_ed25519.*|*.kubeconfig|*.keystore|*.jks|*credentials*|*.secret|*.secrets|.netrc|.npmrc|.pypirc|.htpasswd)
      return 0
      ;;
  esac
  return 1
}

path_is_infrastructure_sensitive() {
  case "$1" in
    .github/workflows/*|Dockerfile|docker-compose*.yml|docker-compose*.yaml|terraform/*|*.tf|k8s/*|kubernetes/*|charts/*|helm/*|vercel.json|infra/*|*/infra/*)
      return 0
      ;;
  esac
  local lower
  lower="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$lower" in
    */ci/*|*/cd/*|*deploy*|*pipeline*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

path_is_high_risk_category() {
  local path="$1"
  local lower
  lower="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"
  case "$lower" in
    auth/*|*/auth/*|*authentication*|*authorization*|*login*|*oauth*|*session*|*jwt*)
      return 0 ;;
    security/*|*/security/*|*encrypt*|*decrypt*|*crypto*|*tls-*|*ssl-*|*/certs/*)
      return 0 ;;
    payments/*|*/payments/*|billing/*|*/billing/*|*stripe*|*invoice*|*subscription*)
      return 0 ;;
    */permissions/*|*/acl/*|*/rbac/*|*access-control*)
      return 0 ;;
  esac
  case "$lower" in
    *migration*|*migrate*|*/db/schema*|alembic/*|*/alembic/*|flyway/*|*/flyway/*|liquibase/*|*/liquibase/*)
      return 0 ;;
  esac
  case "$lower" in
    *destroy*|*teardown*|*drop-*|*nuke*|*purge*|*wipe*)
      return 0 ;;
  esac
  local base_lower
  base_lower="$(basename "$lower")"
  case "$base_lower" in
    package.json|package-lock.json|yarn.lock|pnpm-lock.yaml|go.sum|go.mod|requirements*.txt|pipfile.lock|gemfile.lock|cargo.lock|composer.lock|poetry.lock)
      return 0 ;;
  esac
  return 1
}

wants_auto_merge() {
  local rd="$1"
  local status_json="$rd/status.json"
  if [ ! -f "$status_json" ]; then
    return 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    return 1
  fi
  local flag
  flag="$(jq -r 'if (.auto_merge == true) or (.auto_merge == "true") or (.auto_merge == "True") then "yes" else "no" end' "$status_json")"
  if [ "$flag" = "yes" ]; then
    return 0
  fi
  return 1
}

repo_allows_auto_merge() {
  if [ "${HOCA_TEST_FORCE_REPO_AUTO_MERGE:-}" = "1" ]; then
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    log_skip "GitHub CLI (gh) is not available; cannot verify allow_auto_merge."
    return 1
  fi
  local owner_repo allowed
  owner_repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [ -z "$owner_repo" ]; then
    log_skip "Could not resolve nameWithOwner for this repository."
    return 1
  fi
  allowed="$(gh api "repos/${owner_repo}" --jq .allow_auto_merge 2>/dev/null || echo "false")"
  if [ "$allowed" = "true" ]; then
    return 0
  fi
  log_skip "Repository does not have GitHub allow_auto_merge enabled (Settings → General → Pull Requests)."
  return 1
}

precheck() {
  RUN_DIR="$(cd "$1" && pwd)"
  SKIP_FILE="$RUN_DIR/auto-merge-precheck-skip.txt"
  : > "$SKIP_FILE"

  if ! wants_auto_merge "$RUN_DIR"; then
    rm -f "$SKIP_FILE"
    return 2
  fi

  if [ ! -f "$RUN_DIR/tests-exit-code.txt" ]; then
    log_skip "Missing tests-exit-code.txt; tests must run and record exit code before auto-merge."
    return 1
  fi
  local tex
  tex="$(tr -d '[:space:]' < "$RUN_DIR/tests-exit-code.txt" || true)"
  if [ "$tex" != "0" ]; then
    log_skip "Tests did not pass (tests-exit-code.txt is not 0)."
    return 1
  fi

  if ! PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.review_gate "$RUN_DIR" \
    --review-text "$RUN_DIR/openhands-review.txt" \
    --run-id "$(basename "$RUN_DIR")" \
    --round "${HOCA_REVIEW_ROUND:-1}" >/dev/null; then
    log_skip "Code review gate must approve before auto-merge."
    return 1
  fi

  if [ ! -f "$RUN_DIR/risk-level.txt" ]; then
    log_skip "Missing risk-level.txt; auto-merge requires an explicit machine-readable risk level."
    return 1
  fi
  local rl
  rl="$(head -n 1 "$RUN_DIR/risk-level.txt" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  if [ "$rl" != "low" ]; then
    log_skip "risk-level.txt must be exactly \"low\" (first line); got \"$rl\"."
    return 1
  fi

  if [ ! -f "$RUN_DIR/staged-files.txt" ] || [ ! -s "$RUN_DIR/staged-files.txt" ]; then
    log_skip "Missing or empty staged-files.txt from the commit run."
    return 1
  fi

  local path
  while IFS= read -r path || [ -n "$path" ]; do
    [ -z "$path" ] && continue
    if path_is_secret_like "$path"; then
      log_skip "Secret-like or runtime path in staged commit: $path"
      return 1
    fi
    if path_is_high_risk_category "$path"; then
      log_skip "High-risk category path blocks auto-merge: $path"
      return 1
    fi
    if path_is_infrastructure_sensitive "$path"; then
      local just="$RUN_DIR/staging-justification.txt"
      if [ ! -s "$just" ] || ! grep -Fq "$path" "$just"; then
        log_skip "Infrastructure-sensitive path requires an entry in staging-justification.txt: $path"
        return 1
      fi
    fi
  done < "$RUN_DIR/staged-files.txt"

  if ! repo_allows_auto_merge; then
    return 1
  fi

  return 0
}

postcheck_mergeable() {
  # Current branch's PR must be mergeable (no conflicts with base).
  if [ "${HOCA_TEST_FORCE_PR_MERGEABLE:-}" = "1" ]; then
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    return 1
  fi
  local _attempt mergeable
  for _attempt in $(seq 1 10); do
    mergeable="$(gh pr view --json mergeable -q .mergeable 2>/dev/null || echo "UNKNOWN")"
    if [ "$mergeable" = "MERGEABLE" ]; then
      return 0
    fi
    if [ "$mergeable" = "CONFLICTING" ]; then
      return 1
    fi
    sleep 2
  done
  return 1
}

cmd="${1:-}"
shift || true
case "$cmd" in
  wants-auto-merge)
    wants_auto_merge "$1"
    ;;
  precheck)
    precheck "$1"
    ;;
  postcheck-mergeable)
    postcheck_mergeable
    ;;
  *)
    echo "Usage: auto-merge-guards.sh wants-auto-merge|precheck <run-dir> | postcheck-mergeable" >&2
    exit 2
    ;;
esac
