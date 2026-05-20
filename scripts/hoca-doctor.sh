#!/usr/bin/env bash
set -euo pipefail

FAILED=0
WARNED=0

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

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

env_file_value() {
  local name="$1"

  if [ ! -f ".env" ]; then
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
  ' ".env"
}

config_value() {
  local name="$1"
  local value="${!name:-}"

  if [ -n "$value" ]; then
    printf '%s\n' "$value"
  else
    env_file_value "$name" || true
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

RECOMMENDED_RAM_GB="$(config_value HOCA_RECOMMENDED_RAM_GB)"
RECOMMENDED_RAM_GB="${RECOMMENDED_RAM_GB:-48}"
DEFAULT_MODEL="$(config_value OLLAMA_MODEL)"
DEFAULT_MODEL="${DEFAULT_MODEL:-qwen-14b-pro}"
DOCTOR_LLM_MODEL="$(config_value LLM_MODEL)"
DOCTOR_LLM_MODEL="${DOCTOR_LLM_MODEL:-ollama/qwen-14b-pro}"
DOCTOR_LLM_BASE_URL="$(config_value LLM_BASE_URL)"
DOCTOR_LLM_API_KEY="$(config_value LLM_API_KEY)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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
check_command docker "Install Docker Desktop or Colima."
check_command openhands "Install OpenHands CLI."

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
case "$DOCTOR_LLM_MODEL" in
  ollama/*)
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
          if printf '%s\n' "$OLLAMA_LIST" | awk -v model="$DEFAULT_MODEL" 'NR > 1 && ($1 == model || $1 == model ":latest") { found = 1 } END { exit found ? 0 : 1 }'; then
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
    ;;
  *)
    ok "Skipping Ollama checks (LLM_MODEL is not Ollama-based)."
    ;;
esac

section "OpenHands CLI"
OH_CAPABILITIES=""
if command -v openhands >/dev/null 2>&1; then
  if OPENHANDS_HELP="$(openhands --help 2>&1)"; then
    for flag in --headless --task --override-with-envs; do
      if printf '%s\n' "$OPENHANDS_HELP" | grep -q -- "$flag"; then
        ok "OpenHands supports $flag."
        OH_CAPABILITIES="${OH_CAPABILITIES:+$OH_CAPABILITIES,}${flag#--}"
      else
        fail "OpenHands CLI help does not show $flag."
      fi
    done

    for optional_flag in --json --enable-browsing; do
      if printf '%s\n' "$OPENHANDS_HELP" | grep -q -- "$optional_flag"; then
        ok "OpenHands supports $optional_flag."
        OH_CAPABILITIES="${OH_CAPABILITIES:+$OH_CAPABILITIES,}${optional_flag#--}"
      else
        warn "OpenHands CLI help does not show optional $optional_flag."
      fi
    done

    ok "OpenHands capabilities: ${OH_CAPABILITIES:-none}"
  else
    fail "OpenHands is installed but 'openhands --help' failed."
  fi
else
  warn "Skipping OpenHands flag checks because openhands is missing."
fi

section "LLM Provider"
LLM_MODEL_CHECK="$DOCTOR_LLM_MODEL"
case "$LLM_MODEL_CHECK" in
  ollama/*)
    if command -v ollama >/dev/null 2>&1; then
      ok "Ollama command available (provider: ollama)."
    else
      fail "LLM_MODEL uses Ollama but ollama command not found. Install Ollama."
    fi
    ;;
  openai/*)
    LM_URL="${DOCTOR_LLM_BASE_URL:-http://localhost:1234/v1}"
    if curl -fsS "$LM_URL/models" >/dev/null 2>&1; then
      ok "LM Studio reachable at $LM_URL (provider: openai)."
    else
      warn "LM Studio not reachable at $LM_URL. Ensure LM Studio is running."
    fi
    ;;
  deepseek/*|gemini/*|anthropic/*|together_ai/*|openrouter/*)
    if [ -n "$DOCTOR_LLM_API_KEY" ] && [ "$DOCTOR_LLM_API_KEY" != "ollama" ]; then
      ok "Cloud provider configured: $LLM_MODEL_CHECK (API key set)."
    else
      fail "Cloud provider $LLM_MODEL_CHECK requires LLM_API_KEY to be set."
    fi
    ;;
  *)
    warn "Unknown LLM provider prefix in LLM_MODEL=$LLM_MODEL_CHECK."
    ;;
esac

section "Environment"
if [ -f ".env" ]; then
  if [ -r ".env" ]; then
    ok ".env exists and is readable."
    if grep -nEv '^([[:space:]]*#.*|[[:space:]]*$|[A-Za-z_][A-Za-z0-9_]*=.*)$' ".env" >/dev/null; then
      warn ".env contains lines that are not simple KEY=value assignments."
    else
      ok ".env uses simple KEY=value syntax."
    fi
  else
    fail ".env exists but is not readable."
  fi
else
  warn ".env not found. Copy .env.example to .env if using webhook or notifications."
fi

for var in LLM_MODEL LLM_BASE_URL LLM_API_KEY; do
  if [ -n "$(config_value "$var")" ]; then
    ok "$var is set."
  else
    warn "$var is not set. Wrapper defaults may be used."
  fi
done

WEBHOOK_ENABLED="$(config_value HOCA_WEBHOOK_ENABLED)"
WEBHOOK_URL="$(config_value HOCA_WEBHOOK_URL)"
WEBHOOK_SECRET="$(config_value HOCA_WEBHOOK_SECRET)"
if is_truthy "$WEBHOOK_ENABLED" || [ -n "$WEBHOOK_URL" ]; then
  ok "Webhook mode appears enabled."
  if [ -n "$WEBHOOK_SECRET" ]; then
    ok "HOCA_WEBHOOK_SECRET is set."
  else
    fail "Webhook mode is enabled but HOCA_WEBHOOK_SECRET is not set."
  fi
else
  warn "Webhook mode is not enabled; skipping HOCA_WEBHOOK_SECRET requirement."
fi

TELEGRAM_ENABLED="$(config_value HOCA_NOTIFY_TELEGRAM)"
TELEGRAM_BOT_TOKEN="$(config_value TELEGRAM_BOT_TOKEN)"
TELEGRAM_CHAT_ID="$(config_value TELEGRAM_CHAT_ID)"
if is_truthy "$TELEGRAM_ENABLED" || [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$TELEGRAM_CHAT_ID" ]; then
  ok "Telegram notifications appear enabled."
  if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    ok "TELEGRAM_BOT_TOKEN is set."
  else
    fail "Telegram notifications are enabled but TELEGRAM_BOT_TOKEN is not set."
  fi
  if [ -n "$TELEGRAM_CHAT_ID" ]; then
    ok "TELEGRAM_CHAT_ID is set."
  else
    fail "Telegram notifications are enabled but TELEGRAM_CHAT_ID is not set."
  fi
else
  warn "Telegram notifications are not enabled; skipping Telegram variable requirements."
fi

section "Sandbox"
USE_SANDBOX="$(config_value HOCA_USE_SANDBOX)"
USE_SANDBOX="${USE_SANDBOX:-true}"
SANDBOX_SCRIPT="$SCRIPT_DIR/run-openhands-sandboxed.sh"
SANDBOX_IMAGE="$(config_value HOCA_SANDBOX_IMAGE)"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-hoca-sandbox:latest}"

if is_truthy "$USE_SANDBOX"; then
  ok "HOCA_USE_SANDBOX is enabled (recommended default)."
else
  warn "HOCA_USE_SANDBOX=false: worker/reviewer OpenHands runs on the host (higher risk)."
  warn "Host execution is opt-in only. Prefer sandboxed execution for autonomous rounds."
fi

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    if is_truthy "$USE_SANDBOX"; then
      if [ -x "$SANDBOX_SCRIPT" ]; then
        ok "Sandbox wrapper script is executable: run-openhands-sandboxed.sh"
      else
        fail "HOCA_USE_SANDBOX=true but run-openhands-sandboxed.sh is missing or not executable."
      fi
      if docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
        ok "Sandbox image is available: $SANDBOX_IMAGE"
      else
        warn "Sandbox image not built yet: $SANDBOX_IMAGE (run: scripts/sandbox-manager.sh build)"
      fi
    else
      warn "Docker is available but sandboxing is disabled via HOCA_USE_SANDBOX=false."
    fi
  else
    if is_truthy "$USE_SANDBOX"; then
      fail "HOCA_USE_SANDBOX=true but Docker daemon is not running."
    else
      warn "Docker daemon is not running; host execution will be used."
    fi
  fi
else
  if is_truthy "$USE_SANDBOX"; then
    fail "HOCA_USE_SANDBOX=true but docker command is missing."
  else
    warn "Docker is missing; host execution is the only available path."
  fi
fi

for sandbox_script in run-openhands-sandboxed.sh sandbox-manager.sh; do
  script_path="$SCRIPT_DIR/$sandbox_script"
  if [ ! -f "$script_path" ]; then
    warn "Sandbox script missing: $sandbox_script"
  elif grep -q 'GITHUB_TOKEN' "$script_path"; then
    fail "Sandbox script forwards GITHUB_TOKEN: $sandbox_script"
  else
    ok "Sandbox script does not forward GITHUB_TOKEN: $sandbox_script"
  fi
done

section "Worktree Sandbox"
USE_WORKTREE="$(config_value HOCA_USE_WORKTREE_SANDBOX)"
USE_WORKTREE="${USE_WORKTREE:-true}"
if is_truthy "$USE_WORKTREE"; then
  ok "HOCA_USE_WORKTREE_SANDBOX is enabled. Worker/reviewer use a disposable worktree."
else
  warn "HOCA_USE_WORKTREE_SANDBOX=false: worker/reviewer modify the active checkout directly."
fi

section "Model Pool"
MODEL_POOL_OUTPUT="$(
  PYTHONPATH="$HOCA_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m hoca.role_model_env doctor-checks 2>/dev/null || true
)"
if [ -n "$MODEL_POOL_OUTPUT" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "[OK]"*)
        ok "${line#\[OK\] }"
        ;;
      "[WARN]"*)
        warn "${line#\[WARN\] }"
        ;;
      "[FAIL]"*)
        fail "${line#\[FAIL\] }"
        ;;
    esac
  done <<< "$MODEL_POOL_OUTPUT"
else
  warn "Model pool doctor checks could not run."
fi

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
