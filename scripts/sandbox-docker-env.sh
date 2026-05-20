#!/usr/bin/env bash
# Shared Docker sandbox runtime helpers for worker/reviewer containers.

# Resolve the user for `docker run --user`.
# Default: project directory owner uid:gid so the mounted worktree stays writable.
# Override with HOCA_SANDBOX_USER=worker to force the image's non-root account.
sandbox_resolve_user() {
  local project_path="$1"
  if [ -n "${HOCA_SANDBOX_USER:-}" ]; then
    printf '%s' "$HOCA_SANDBOX_USER"
    return 0
  fi
  if stat -c '%u' "$project_path" >/dev/null 2>&1; then
    printf '%s:%s' "$(stat -c '%u' "$project_path")" "$(stat -c '%g' "$project_path")"
  else
    printf '%s:%s' "$(stat -f '%u' "$project_path")" "$(stat -f '%g' "$project_path")"
  fi
}

# Prepare a writable HOME directory for the sandbox user (bind-mounted into the container).
sandbox_prepare_home() {
  local run_dir="$1"
  local home_dir="$run_dir/sandbox-home"
  mkdir -p "$home_dir"
  printf '%s' "$home_dir"
}

# Resolve effective sandbox network mode (worker/reviewer) via hoca.sandbox_network.
sandbox_resolve_network_mode() {
  local role="${1:-worker}"
  local run_dir="${2:-}"
  local resolve_args=(--role "$role")
  if [ -n "$run_dir" ]; then
    resolve_args+=(--run-dir "$run_dir")
  fi
  if [ -n "${HOCA_NETWORK_MODE:-}" ]; then
    resolve_args+=(--env-mode "$HOCA_NETWORK_MODE")
  fi
  PYTHONPATH="${HOCA_ROOT:?HOCA_ROOT must be set}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m hoca.sandbox_network resolve "${resolve_args[@]}"
}

# Print docker run network flags for a resolved mode.
sandbox_docker_network_args() {
  local mode="$1"
  PYTHONPATH="${HOCA_ROOT:?HOCA_ROOT must be set}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m hoca.sandbox_network docker-args --mode "$mode"
}

# Record effective network policy into sandbox-policy.json for the run.
sandbox_record_network_policy() {
  local role="$1"
  local run_dir="$2"
  PYTHONPATH="${HOCA_ROOT:?HOCA_ROOT must be set}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m hoca.sandbox_network record --role "$role" --run-dir "$run_dir" \
    ${HOCA_NETWORK_MODE:+--env-mode "$HOCA_NETWORK_MODE"} >/dev/null
}
