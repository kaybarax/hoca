#!/usr/bin/env bash
set -euo pipefail

FAILED=0
WARNED=0
RECOMMENDED_RAM_GB="${HOCA_RECOMMENDED_RAM_GB:-48}"
DEFAULT_MODEL="${OLLAMA_MODEL:-qwen-32b-pro}"

ok() {
  printf '[OK] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
  WARNED=1
}

fail() {
  printf '[FAIL] %s\n' "$1"
  FAILED=1
}

section() {
  printf '\n%s\n' "$1"
  printf '%s\n' "----------------------------------------"
}

check_command() {
  local cmd="$1"
  local hint="$2"

  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$cmd found: $(command -v "$cmd")"
  else
    fail "$cmd not found. $hint"
  fi
}

detect_ram_gb() {
  case "$(uname -s)" in
    Darwin)
      if command -v sysctl >/dev/null 2>&1; then
        sysctl -n hw.memsize 2>/dev/null | awk '{ printf "%.0f", $1 / 1024 / 1024 / 1024 }'
      fi
      ;;
    Linux)
      if [ -r /proc/meminfo ]; then
        awk '/MemTotal/ { printf "%.0f", $2 / 1024 / 1024 }' /proc/meminfo
      fi
      ;;
  esac
}

echo "HOCA Doctor"
echo "==========="

section "Host"
OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
case "$OS_NAME" in
  Darwin)
    ok "Operating system: macOS"
    ;;
  Linux)
    ok "Operating system: Linux"
    ;;
  *)
    warn "Operating system is not a primary HOCA target: $OS_NAME"
    ;;
esac

case "$ARCH_NAME" in
  arm64|aarch64|x86_64)
    ok "CPU architecture: $ARCH_NAME"
    ;;
  *)
    warn "CPU architecture is unusual for HOCA: $ARCH_NAME"
    ;;
esac

RAM_GB="$(detect_ram_gb || true)"
if [ -n "${RAM_GB:-}" ]; then
  ok "Detected RAM: ${RAM_GB} GB"
  if [ "$RAM_GB" -lt "$RECOMMENDED_RAM_GB" ]; then
    warn "RAM is below ${RECOMMENDED_RAM_GB} GB. Prefer 7B or 14B Ollama models over 32B models."
  fi
else
  warn "Could not determine system RAM."
fi

section "Required Binaries"
check_command git "Install Git."
check_command gh "Install GitHub CLI: brew install gh"
check_command python3 "Install Python 3.12+."
check_command node "Install Node.js."
check_command jq "Install jq: brew install jq"
check_command curl "Install curl."
check_command openssl "Install OpenSSL."
check_command ollama "Install Ollama."
check_command docker "Install Docker Desktop or Colima."
check_command openhands "Install OpenHands CLI."
check_command aider "Install Aider."

section "GitHub CLI"
if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    ok "GitHub CLI is authenticated."
  else
    fail "GitHub CLI is not authenticated. Run: gh auth login"
  fi
else
  warn "Skipping GitHub authentication check because gh is missing."
fi

section "Docker"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon is running."
  else
    fail "Docker is installed but the daemon is not running."
  fi
else
  warn "Skipping Docker daemon check because docker is missing."
fi

section "Ollama"
if command -v ollama >/dev/null 2>&1; then
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama server is reachable at http://127.0.0.1:11434."
  else
    fail "Ollama server is not reachable. Start it with: ollama serve"
  fi

  if OLLAMA_LIST="$(ollama list 2>/dev/null)"; then
    MODEL_COUNT="$(printf '%s\n' "$OLLAMA_LIST" | awk 'NR > 1 && NF > 0 { count++ } END { print count + 0 }')"
    if [ "$MODEL_COUNT" -gt 0 ]; then
      ok "Ollama models available: $MODEL_COUNT"
      if printf '%s\n' "$OLLAMA_LIST" | awk 'NR > 1 { print $1 }' | grep -qx "$DEFAULT_MODEL"; then
        ok "Default Ollama model found: $DEFAULT_MODEL"
      else
        warn "Default Ollama model not found: $DEFAULT_MODEL"
        warn "Build it with: ollama create $DEFAULT_MODEL -f ./models/Modelfile"
      fi
    else
      warn "No Ollama models are installed. Run: ollama pull qwen2.5-coder:7b"
    fi
  else
    warn "Could not list Ollama models."
  fi
else
  warn "Skipping Ollama checks because ollama is missing."
fi

section "OpenHands CLI"
if command -v openhands >/dev/null 2>&1; then
  if OPENHANDS_HELP="$(openhands --help 2>&1)"; then
    for flag in --headless --task --override-with-envs; do
      if printf '%s\n' "$OPENHANDS_HELP" | grep -q -- "$flag"; then
        ok "OpenHands supports $flag."
      else
        fail "OpenHands CLI help does not show $flag."
      fi
    done

    if printf '%s\n' "$OPENHANDS_HELP" | grep -q -- "--json"; then
      ok "OpenHands supports --json."
    else
      warn "OpenHands CLI help does not show optional --json. JSON logging may be unavailable."
    fi
  else
    fail "OpenHands is installed but 'openhands --help' failed."
  fi
else
  warn "Skipping OpenHands flag checks because openhands is missing."
fi

section "Aider"
if command -v aider >/dev/null 2>&1; then
  if aider --version >/dev/null 2>&1; then
    ok "Aider can run."
  else
    fail "Aider is installed but 'aider --version' failed."
  fi
else
  warn "Skipping Aider runtime check because aider is missing."
fi

section "Environment"
if [ -f ".env" ]; then
  ok ".env exists."
else
  warn ".env not found. Copy .env.example to .env if using webhook or notifications."
fi

for var in LLM_MODEL LLM_BASE_URL LLM_API_KEY; do
  if [ -n "${!var:-}" ]; then
    ok "$var is set."
  else
    warn "$var is not set. Wrapper defaults may be used."
  fi
done

section "Summary"
if [ "$FAILED" -eq 0 ]; then
  if [ "$WARNED" -eq 0 ]; then
    ok "HOCA Doctor completed without warnings or critical failures."
  else
    ok "HOCA Doctor completed with warnings."
  fi
else
  fail "HOCA Doctor found critical failures."
  exit 1
fi
