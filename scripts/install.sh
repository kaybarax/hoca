#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FAILED=0
BREW_PACKAGES=(
  git
  gh
  python@3.12
  node
  pipx
  jq
  curl
  openssl
  ollama
)

info() {
  printf '%s\n' "$*"
}

warn() {
  printf 'WARNING: %s\n' "$*" >&2
}

fail_soft() {
  warn "$*"
  FAILED=1
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    info "HOCA dependency installation currently targets macOS."
    info "Detected: $(uname -s)"
    exit 1
  fi
}

require_homebrew() {
  if ! command -v brew >/dev/null 2>&1; then
    info "Homebrew is required to install HOCA dependencies."
    info "Install Homebrew first: https://brew.sh"
    exit 1
  fi
}

install_brew_package() {
  local package="$1"

  if brew list --formula "$package" >/dev/null 2>&1; then
    info "Found Homebrew package: $package"
    return
  fi

  info "Installing Homebrew package: $package"
  brew install "$package"
}

check_command() {
  local command_name="$1"
  local guidance="$2"

  if command -v "$command_name" >/dev/null 2>&1; then
    info "Found $command_name: $(command -v "$command_name")"
  else
    fail_soft "$command_name is missing. $guidance"
  fi
}

python_bin() {
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    return 1
  fi
}

venv_python_bin() {
  printf '%s\n' "$REPO_ROOT/.venv/bin/python"
}

ensure_hoca_venv() {
  local py_bin
  local venv_py

  py_bin="$(python_bin)" || {
    fail_soft "Python 3.12+ is required, but no python3 executable was found."
    return 1
  }

  venv_py="$(venv_python_bin)"
  if [[ ! -x "$venv_py" ]]; then
    info "Creating HOCA Python virtual environment: $REPO_ROOT/.venv"
    "$py_bin" -m venv "$REPO_ROOT/.venv"
  fi

  if [[ ! -x "$venv_py" ]]; then
    fail_soft "HOCA virtual environment was not created successfully."
    return 1
  fi
}

install_python_dependencies() {
  local venv_py

  ensure_hoca_venv || {
    return
  }

  venv_py="$(venv_python_bin)"
  info "Installing HOCA Python package dependencies with $venv_py"
  "$venv_py" -m pip install --upgrade pip
  "$venv_py" -m pip install --upgrade -e "$REPO_ROOT"
}


install_openhands() {
  if command -v openhands >/dev/null 2>&1; then
    info "Found OpenHands CLI: $(command -v openhands)"
    return
  fi

  info "Installing OpenHands CLI."
  curl -fsSL https://install.openhands.dev/install.sh | sh
}

check_docker() {
  if command -v docker >/dev/null 2>&1; then
    info "Found docker: $(command -v docker)"
    if docker info >/dev/null 2>&1; then
      info "Docker daemon is available."
    else
      fail_soft "Docker is installed but not running. Start Docker Desktop or Colima before running HOCA tasks."
    fi
    return
  fi

  fail_soft "Docker is missing. Install Docker Desktop or Colima, then start the Docker daemon."
  info "Docker Desktop: https://www.docker.com/products/docker-desktop/"
  info "Colima: brew install colima docker && colima start"
}

check_github_auth() {
  if ! command -v gh >/dev/null 2>&1; then
    fail_soft "GitHub CLI is missing, so authentication cannot be checked."
    return
  fi

  if gh auth status >/dev/null 2>&1; then
    info "GitHub CLI is authenticated."
  else
    fail_soft "GitHub CLI is not authenticated."
    info "Run this manually when ready: gh auth login"
  fi
}

ollama_server_responds() {
  curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1
}

ollama_has_model() {
  local model="$1"

  ollama list 2>/dev/null | awk -v model="$model" 'NR > 1 && ($1 == model || $1 == model ":latest") { found = 1 } END { exit found ? 0 : 1 }'
}

pull_ollama_model() {
  local model="$1"
  local required="$2"

  if ollama_has_model "$model"; then
    info "Found Ollama model: $model"
    return
  fi

  info "Pulling Ollama model: $model"
  if ollama pull "$model"; then
    info "Pulled Ollama model: $model"
    return
  fi

  if [[ "$required" == "required" ]]; then
    fail_soft "Could not pull required Ollama model: $model"
  else
    warn "Could not pull optional Ollama model: $model"
  fi
}

create_ollama_alias() {
  local alias_name="$1"
  local modelfile="$2"
  local required="$3"

  if [[ ! -f "$modelfile" ]]; then
    if [[ "$required" == "required" ]]; then
      fail_soft "Missing Modelfile for $alias_name: $modelfile"
    else
      warn "Missing Modelfile for optional alias $alias_name: $modelfile"
    fi
    return
  fi

  info "Creating Ollama model alias: $alias_name"
  if ollama create "$alias_name" -f "$modelfile"; then
    info "Created Ollama model alias: $alias_name"
    return
  fi

  if [[ "$required" == "required" ]]; then
    fail_soft "Could not create required Ollama model alias: $alias_name"
  else
    warn "Could not create optional Ollama model alias: $alias_name"
  fi
}

print_model_fallback_status() {
  local selected_model=""
  local alias_name

  info ""
  info "Ollama model fallback status:"
  for alias_name in qwen-14b-pro qwen-7b-pro qwen-32b-pro; do
    if ollama_has_model "$alias_name"; then
      info "- $alias_name available"
      if [[ -z "$selected_model" ]]; then
        selected_model="$alias_name"
      fi
    else
      info "- $alias_name unavailable"
    fi
  done

  if [[ -n "$selected_model" ]]; then
    info "Selected fallback model: $selected_model"
  else
    fail_soft "No HOCA Ollama model aliases are available."
  fi
}

setup_ollama_models() {
  info "Checking Ollama runtime..."

  if ! command -v ollama >/dev/null 2>&1; then
    fail_soft "Ollama is missing. Install it with: brew install ollama"
    return
  fi

  info "Found ollama: $(command -v ollama)"

  if ! ollama_server_responds; then
    fail_soft "Ollama server is not responding at http://127.0.0.1:11434/api/tags"
    info "Start Ollama manually with: ollama serve"
    return
  fi

  info "Ollama server responded at http://127.0.0.1:11434/api/tags"

  pull_ollama_model qwen2.5-coder:32b optional
  pull_ollama_model qwen2.5-coder:14b required
  pull_ollama_model qwen2.5-coder:7b required

  create_ollama_alias qwen-32b-pro "$REPO_ROOT/models/Modelfile" optional
  create_ollama_alias qwen-14b-pro "$REPO_ROOT/models/Modelfile.14b" required
  create_ollama_alias qwen-7b-pro "$REPO_ROOT/models/Modelfile.7b" required

  print_model_fallback_status
}

main() {
  info "Installing HOCA dependencies..."
  info ""

  require_macos
  require_homebrew

  for package in "${BREW_PACKAGES[@]}"; do
    install_brew_package "$package"
  done

  info ""
  info "Verifying installed commands..."
  check_command git "Install Git with: brew install git"
  check_command gh "Install GitHub CLI with: brew install gh"
  check_command python3.12 "Install Python 3.12 with: brew install python@3.12"
  check_command node "Install Node.js with: brew install node"
  check_command pipx "Install pipx with: brew install pipx"
  check_command jq "Install jq with: brew install jq"
  check_command curl "Install curl with: brew install curl"
  check_command openssl "Install OpenSSL with: brew install openssl"
  check_command ollama "Install Ollama with: brew install ollama"

  info ""
  check_docker

  info ""
  install_python_dependencies
  install_openhands

  info ""
  setup_ollama_models

  info ""
  check_github_auth

  info ""
  if [[ "$FAILED" -eq 0 ]]; then
    info "HOCA dependency installation complete."
  else
    info "HOCA dependency installation finished with manual follow-up items."
    exit 1
  fi

  info ""
  info "Next steps:"
  info "1. Copy .env.example to .env and fill in local values."
  info "2. Start Ollama with: ollama serve"
  info "3. Start Docker Desktop or Colima."
}

main "$@"
