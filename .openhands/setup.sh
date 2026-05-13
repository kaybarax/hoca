#!/usr/bin/env bash
set -euo pipefail

echo "Initializing OpenHands sandbox..."

echo "  Working directory: $(pwd)"
echo "  User: $(whoami)"
echo "  Shell: $SHELL"
echo "  PATH: $PATH"

if [ -f "package.json" ]; then
  echo "Detected Node project."
  if [ -f "pnpm-lock.yaml" ]; then
    corepack enable
    pnpm install --frozen-lockfile
  elif [ -f "yarn.lock" ]; then
    corepack enable
    yarn install --frozen-lockfile
  elif [ -f "package-lock.json" ]; then
    npm ci
  else
    npm install
  fi
fi

if [ -f "requirements.txt" ]; then
  echo "Detected Python requirements.txt."
  python -m pip install -r requirements.txt
fi

if [ -f "pyproject.toml" ]; then
  echo "Detected Python pyproject.toml."
  python -m pip install -e . || true
fi

if [ -f "go.mod" ]; then
  echo "Detected Go project."
  go mod download
fi

if [ -f "Cargo.toml" ]; then
  echo "Detected Rust project."
  cargo fetch
fi

echo "OpenHands sandbox is ready."
