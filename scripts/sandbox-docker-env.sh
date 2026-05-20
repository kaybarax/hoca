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
