#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FAILED=0
BREW_PACKAGES=(
  git
  gh
  python@3.12
  node
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

install_python_dependencies() {
  local py_bin

  py_bin="$(python_bin)" || {
    fail_soft "Python 3.12+ is required, but no python3 executable was found."
    return
  }

  info "Installing HOCA Python package dependencies with $py_bin"
  if ! "$py_bin" -m pip install --upgrade -e "$REPO_ROOT"; then
    info "Retrying Python dependency install with --user."
    "$py_bin" -m pip install --user --upgrade -e "$REPO_ROOT"
  fi
}

install_aider() {
  local py_bin

  if command -v aider >/dev/null 2>&1; then
    info "Found aider: $(command -v aider)"
    return
  fi

  py_bin="$(python_bin)" || {
    fail_soft "Cannot install Aider because Python is missing."
    return
  }

  info "Installing Aider."
  "$py_bin" -m pip install --user --upgrade aider-install
  if command -v aider-install >/dev/null 2>&1; then
    aider-install
  else
    fail_soft "aider-install was installed but is not on PATH. Add your Python user bin directory to PATH and rerun this script."
  fi
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
  check_command jq "Install jq with: brew install jq"
  check_command curl "Install curl with: brew install curl"
  check_command openssl "Install OpenSSL with: brew install openssl"
  check_command ollama "Install Ollama with: brew install ollama"

  info ""
  check_docker

  info ""
  install_python_dependencies
  install_aider
  install_openhands

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
