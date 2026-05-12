#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -eq 0 ]]; then
  echo "Usage: stage-safe-files.sh <path> [path ...]" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$REPO_ROOT" python3 - "$@" <<'PY'
import sys

from hoca.config import PolicyError
from hoca.git_utils import build_stage_command

try:
    build_stage_command(sys.argv[1:])
except PolicyError as exc:
    print(f"Refusing to stage files: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY

git add -- "$@"
