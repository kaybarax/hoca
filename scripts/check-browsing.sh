#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: check-browsing.sh /path/to/run-dir [--require]"
  echo ""
  echo "Checks whether OpenHands browsing capability is available."
  echo "With --require, exits non-zero and prints guidance when browsing is unavailable."
  exit 1
fi

RUN_DIR="$1"
REQUIRE=false
if [ "${2:-}" = "--require" ]; then
  REQUIRE=true
fi

CAPS_FILE="$RUN_DIR/openhands-capabilities.txt"
BROWSING_AVAILABLE=false

if [ -f "$CAPS_FILE" ]; then
  if grep -q "enable-browsing" "$CAPS_FILE"; then
    BROWSING_AVAILABLE=true
  fi
fi

if ! command -v openhands >/dev/null 2>&1; then
  BROWSING_AVAILABLE=false
elif [ ! -f "$CAPS_FILE" ]; then
  OH_HELP="$(openhands --help 2>&1 || true)"
  if printf '%s\n' "$OH_HELP" | grep -q -- "--enable-browsing"; then
    BROWSING_AVAILABLE=true
  fi
fi

printf '%s\n' "$BROWSING_AVAILABLE" > "$RUN_DIR/browsing-available.txt"

if [ "$BROWSING_AVAILABLE" = true ]; then
  echo "OpenHands browsing capability is available."
  exit 0
fi

echo "OpenHands browsing capability is not available."

if [ "$REQUIRE" = true ]; then
  cat <<'GUIDANCE'

This task requires web research but no browsing capability was detected.

To proceed, the engineer should provide the needed source material directly:
  1. Add URLs or reference content to a file named research-sources.txt in the run directory.
  2. Re-run the task after providing the material.

Alternatively, if Hermes has its own browsing tool, it can perform the research
directly and pass findings to OpenHands via the task description.
GUIDANCE
  exit 1
fi

exit 0
