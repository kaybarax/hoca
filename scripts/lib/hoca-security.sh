#!/usr/bin/env bash
# Shared helpers for shell scripts to reuse hoca.security checks.

if [ -z "${HOCA_ROOT:-}" ]; then
  _HOCA_SECURITY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  HOCA_ROOT="$(cd "$_HOCA_SECURITY_LIB_DIR/../.." && pwd)"
fi

_hoca_security_python() {
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m hoca.security_cli "$@"
}

hoca_path_is_secret_like() {
  _hoca_security_python is-secret-like "$1" >/dev/null 2>&1
}

hoca_validate_staging_path() {
  local repo_root="$1"
  local path="$2"
  _hoca_security_python validate-path "$repo_root" "$path"
}

hoca_validate_staging_file_list() {
  local repo_root="$1"
  local file_list="$2"
  _hoca_security_python validate-staging "$repo_root" "$file_list"
}
