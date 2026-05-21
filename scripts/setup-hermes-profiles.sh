#!/usr/bin/env bash
set -euo pipefail

# Install HOCA Hermes role profiles (manager, worker, reviewer) from repo templates.
# Idempotent: preserves existing user-modified SOUL.md and config.yaml content.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILES_TEMPLATE_DIR="$REPO_ROOT/hermes-profiles"
HERMES_SKILLS_DIR="$REPO_ROOT/hermes-skills"
REPORT_DIR="$REPO_ROOT/.hoca-runtime"
REPORT_FILE="$REPORT_DIR/setup-hermes-profiles-report.txt"

PROFILE_NAMES=(hoca-manager hoca-worker hoca-reviewer)
DRY_RUN=0
FAILED=0
WARNED=0

DEFAULT_HERMES_SOUL=$'You are Hermes Agent, an intelligent AI assistant created by Nous Research. You are helpful, knowledgeable, and direct. You assist users with a wide range of tasks including answering questions, writing and editing code, analyzing information, creative work, and executing actions via your tools. You communicate clearly, admit uncertainty when appropriate, and prioritize being genuinely useful over being verbose unless otherwise directed below. Be targeted and efficient in your exploration and investigations.'

usage() {
  cat <<'EOF'
Usage: setup-hermes-profiles.sh [options]

Install or update HOCA Hermes role profiles from hermes-profiles/ templates.

Options:
  --dry-run     Print planned actions without changing files or running Hermes.
  --report FILE Write the setup report to FILE (default: .hoca-runtime/setup-hermes-profiles-report.txt)
  -h, --help    Show this help message.

Environment:
  HERMES_HOME           Hermes home directory (default: ~/.hermes)
  HOCA_WORKSPACE_ROOT   Target repositories root for rendered config paths
                        (default: value from .env or parent of the HOCA repo)
EOF
}

log() {
  printf '%s\n' "$*"
}

report() {
  REPORT_LINES+=("$*")
  log "$*"
}

ok() {
  report "[OK] $1"
}

warn() {
  report "[WARN] $1"
  WARNED=1
}

fail() {
  report "[FAIL] $1"
  FAILED=1
}

run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    report "[DRY-RUN] $*"
    return 0
  fi
  "$@"
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{ print $1 }'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{ print $1 }'
  else
    return 1
  fi
}

sha256_text() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | awk '{ print $1 }'
  elif command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | awk '{ print $1 }'
  else
    return 1
  fi
}

env_file_value() {
  local name="$1"
  local env_path="$REPO_ROOT/.env"

  if [ ! -f "$env_path" ]; then
    return 1
  fi

  awk -F= -v name="$name" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /^[A-Za-z_][A-Za-z0-9_]*=/ { next }
    $1 == name {
      value = substr($0, length($1) + 2)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^"|"$/, "", value)
      gsub(/^'\''|'\''$/, "", value)
      print value
      found = 1
      exit
    }
    END { if (!found) exit 1 }
  ' "$env_path"
}

resolve_path() {
  local path="$1"
  case "$path" in
    \~/*)
      printf '%s\n' "${HOME}${path#\~}"
      ;;
    "~")
      printf '%s\n' "$HOME"
      ;;
    *)
      printf '%s\n' "$path"
      ;;
  esac
}

hermes_home() {
  local home="${HERMES_HOME:-$HOME/.hermes}"
  resolve_path "$home"
}

profile_dir() {
  local profile_name="$1"
  printf '%s/profiles/%s\n' "$(hermes_home)" "$profile_name"
}

profile_exists() {
  local profile_name="$1"
  [ -d "$(profile_dir "$profile_name")" ]
}

hermes_installed() {
  command -v hermes >/dev/null 2>&1
}

profile_commands_available() {
  hermes profile list -h >/dev/null 2>&1 \
    && hermes profile create -h >/dev/null 2>&1 \
    && hermes profile show -h >/dev/null 2>&1
}

render_config_template() {
  local src="$1"
  sed \
    -e "s|~/<path-to-workspace>/hoca|$HOCA_ROOT|g" \
    -e "s|~/<path-to-target-repos>|$HOCA_WORKSPACE_ROOT|g" \
    "$src"
}

workspace_root_default() {
  if [ -n "${HOCA_WORKSPACE_ROOT:-}" ]; then
    resolve_path "$HOCA_WORKSPACE_ROOT"
    return
  fi

  if value="$(env_file_value HOCA_WORKSPACE_ROOT)"; then
    resolve_path "$value"
    return
  fi

  resolve_path "$(dirname "$REPO_ROOT")"
}

soul_is_installable() {
  local soul_file="$1"
  local template_file="$2"

  if [ ! -f "$soul_file" ]; then
    return 0
  fi

  if cmp -s "$soul_file" "$template_file"; then
    return 0
  fi

  if [ "$(cat "$soul_file")" = "$DEFAULT_HERMES_SOUL" ]; then
    return 0
  fi

  return 1
}

install_soul() {
  local profile_name="$1"
  local profile_path="$2"
  local template_file="$PROFILES_TEMPLATE_DIR/$profile_name/SOUL.md"
  local soul_file="$profile_path/SOUL.md"
  local marker_file="$profile_path/.hoca-soul-installed"
  local template_hash

  if [ ! -f "$template_file" ]; then
    fail "Missing SOUL template for $profile_name: $template_file"
    return
  fi

  template_hash="$(sha256_file "$template_file")" || {
    fail "Could not hash SOUL template for $profile_name."
    return
  }

  if soul_is_installable "$soul_file" "$template_file"; then
    if [ -f "$soul_file" ] && cmp -s "$soul_file" "$template_file"; then
      ok "$profile_name SOUL.md already matches HOCA template."
    else
      if [ -f "$soul_file" ] && [ "$DRY_RUN" -eq 0 ]; then
        ok "$profile_name SOUL.md replaced default Hermes identity with HOCA template."
      else
        ok "$profile_name SOUL.md will be installed from HOCA template."
      fi
      if [ "$DRY_RUN" -eq 1 ]; then
        report "[DRY-RUN] install $template_file -> $soul_file"
      else
        cp "$template_file" "$soul_file"
        printf '%s\n' "$template_hash" > "$marker_file"
      fi
    fi
    return
  fi

  if [ -f "$marker_file" ] && [ "$(cat "$marker_file")" = "$template_hash" ]; then
    ok "$profile_name SOUL.md preserved (matches prior HOCA install marker)."
    return
  fi

  warn "$profile_name SOUL.md preserved (appears user-modified)."
  if [ "$DRY_RUN" -eq 1 ]; then
    report "[DRY-RUN] would back up $soul_file before any forced refresh"
  fi
}

config_has_skills_path() {
  local config_file="$1"
  grep -qF "$HERMES_SKILLS_DIR" "$config_file"
}

install_config() {
  local profile_name="$1"
  local profile_path="$2"
  local template_file="$PROFILES_TEMPLATE_DIR/$profile_name/config.example.yaml"
  local config_file="$profile_path/config.yaml"

  if [ ! -f "$template_file" ]; then
    fail "Missing config template for $profile_name: $template_file"
    return
  fi

  if [ ! -f "$config_file" ]; then
    ok "$profile_name config.yaml will be created from template."
    if [ "$DRY_RUN" -eq 1 ]; then
      report "[DRY-RUN] render $template_file -> $config_file"
    else
      render_config_template "$template_file" > "$config_file"
    fi
    return
  fi

  if config_has_skills_path "$config_file"; then
    ok "$profile_name config.yaml already references HOCA hermes-skills."
    return
  fi

  ok "$profile_name config.yaml will gain HOCA external skills path."
  if [ "$DRY_RUN" -eq 1 ]; then
    report "[DRY-RUN] append skills.external_dirs to $config_file"
    return
  fi

  {
    printf '\n'
    printf '# Added by HOCA setup-hermes-profiles.sh\n'
    printf 'skills:\n'
    printf '  external_dirs:\n'
    printf '    - "%s"\n' "$HERMES_SKILLS_DIR"
  } >> "$config_file"
}

create_profile_if_missing() {
  local profile_name="$1"

  if profile_exists "$profile_name"; then
    ok "Hermes profile exists: $profile_name"
    return
  fi

  ok "Hermes profile will be created: $profile_name"
  if [ "$DRY_RUN" -eq 1 ]; then
    report "[DRY-RUN] hermes profile create $profile_name --no-alias --no-skills"
    return
  fi

  if ! run_cmd env HERMES_HOME="$(hermes_home)" hermes profile create "$profile_name" --no-alias --no-skills; then
    fail "Could not create Hermes profile: $profile_name"
  fi
}

setup_profile() {
  local profile_name="$1"
  local profile_path

  create_profile_if_missing "$profile_name"
  profile_path="$(profile_dir "$profile_name")"

  if [ "$DRY_RUN" -eq 0 ] && ! profile_exists "$profile_name"; then
    fail "Profile directory missing after create: $profile_path"
    return
  fi

  if [ "$DRY_RUN" -eq 1 ] && ! profile_exists "$profile_name"; then
    profile_path="$(profile_dir "$profile_name")"
    report "[DRY-RUN] would configure profile at $profile_path"
  fi

  install_soul "$profile_name" "$profile_path"
  install_config "$profile_name" "$profile_path"
}

write_report_file() {
  if [ "$DRY_RUN" -eq 1 ]; then
    return
  fi

  mkdir -p "$REPORT_DIR"
  {
    printf 'HOCA Hermes profile setup report\n'
    printf 'Generated: %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf 'HERMES_HOME: %s\n' "$(hermes_home)"
    printf 'HOCA_ROOT: %s\n' "$REPO_ROOT"
    printf 'HOCA_WORKSPACE_ROOT: %s\n' "$HOCA_WORKSPACE_ROOT"
    printf 'HERMES_SKILLS_DIR: %s\n' "$HERMES_SKILLS_DIR"
    printf '\n'
    printf '%s\n' "${REPORT_LINES[@]}"
  } > "$REPORT_FILE"
  ok "Wrote setup report: $REPORT_FILE"
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1
        ;;
      --report)
        shift
        REPORT_FILE="$1"
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"

  HOCA_ROOT="$REPO_ROOT"
  HOCA_WORKSPACE_ROOT="$(workspace_root_default)"
  REPORT_LINES=()

  log "HOCA Hermes profile setup"
  log "========================="
  log "HOCA repo:           $HOCA_ROOT"
  log "Hermes home:         $(hermes_home)"
  log "Workspace root:      $HOCA_WORKSPACE_ROOT"
  log "Hermes skills path:  $HERMES_SKILLS_DIR"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "Mode:                dry-run"
  fi
  log ""

  if ! hermes_installed; then
    fail "hermes CLI not found. Install Hermes Agent and ensure 'hermes' is on PATH."
    fail "See: https://github.com/NousResearch/hermes-agent"
    write_report_file
    exit 1
  fi
  ok "hermes CLI found: $(command -v hermes)"

  if ! profile_commands_available; then
    fail "This hermes install does not expose profile subcommands (list/create/show)."
    fail "Upgrade Hermes Agent, then rerun setup."
    fail "Try: hermes profile --help"
    write_report_file
    exit 1
  fi
  ok "Hermes profile commands are available."

  if [ ! -d "$PROFILES_TEMPLATE_DIR" ]; then
    fail "Missing profile templates directory: $PROFILES_TEMPLATE_DIR"
    write_report_file
    exit 1
  fi

  if [ ! -d "$HERMES_SKILLS_DIR" ]; then
    warn "HOCA hermes-skills directory not found: $HERMES_SKILLS_DIR"
  else
    ok "HOCA hermes-skills directory found."
  fi

  log ""
  for profile_name in "${PROFILE_NAMES[@]}"; do
    log "Profile: $profile_name"
    log "----------------"
    setup_profile "$profile_name"
    log ""
  done

  log "Summary"
  log "-------"
  if [ "$FAILED" -eq 0 ]; then
    if [ "$WARNED" -eq 0 ]; then
      ok "Hermes profile setup completed."
    else
      ok "Hermes profile setup completed with warnings."
    fi
    log ""
    log "Next steps:"
    log "  1. Review rendered config.yaml files under $(hermes_home)/profiles/"
    log "  2. Replace <target-repo> placeholders in docker_volumes with real repo paths."
    log "  3. Run per-profile setup if API keys are needed: hermes -p hoca-manager setup"
    log "  4. Verify profiles: hermes -p hoca-manager doctor"
    log "  5. Enable profiles in .env: HOCA_USE_HERMES_PROFILES=true"
  else
    fail "Hermes profile setup finished with errors."
  fi

  write_report_file

  if [ "$FAILED" -ne 0 ]; then
    exit 1
  fi
}

main "$@"
