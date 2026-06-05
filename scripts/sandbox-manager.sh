#!/usr/bin/env bash
set -euo pipefail

# Manages the Docker sandbox container for HOCA worker runs.
# Builds the sandbox image if needed, starts/stops containers,
# and provides exec access for running commands inside.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOCA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/sandbox-docker-env.sh
source "$SCRIPT_DIR/sandbox-docker-env.sh"

SANDBOX_IMAGE="${HOCA_SANDBOX_IMAGE:-hoca-sandbox:latest}"
SANDBOX_CONTAINER_PREFIX="hoca-worker"

sandbox_image_exists() {
  docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1
}

sandbox_build() {
  echo "Building HOCA sandbox image..."
  docker build \
    -t "$SANDBOX_IMAGE" \
    -f "$HOCA_ROOT/docker/Dockerfile.sandbox" \
    "$HOCA_ROOT/docker"
  echo "Sandbox image built: $SANDBOX_IMAGE"
}

sandbox_ensure_image() {
  if ! sandbox_image_exists; then
    sandbox_build
  fi
}

sandbox_start() {
  local project_path="$1"
  local run_id="${2:-$(date -u +%Y%m%dT%H%M%SZ)}"
  local agent_role="${3:-worker}"
  local run_dir="${4:-}"
  local container_name="${SANDBOX_CONTAINER_PREFIX}-${run_id}"

  sandbox_ensure_image

  # Resolve project path
  project_path="$(cd "$project_path" && pwd)"
  local sandbox_user
  sandbox_user="$(sandbox_resolve_user "$project_path")"
  local sandbox_home
  sandbox_home="$(sandbox_prepare_home "${HOCA_ROOT}/.hoca-runtime/sandbox/${run_id}")"

  # Determine host-local LLM base URL for container access. This may be Ollama,
  # LM Studio, llama.cpp, MLX, LocalAI, vLLM, or another OpenAI-compatible server.
  local llm_url="${LLM_BASE_URL:-http://127.0.0.1:11434}"
  # If the LLM server is on localhost, remap to host.docker.internal for the container.
  llm_url="${llm_url//127.0.0.1/host.docker.internal}"
  llm_url="${llm_url//localhost/host.docker.internal}"

  local network_mode
  network_mode="$(sandbox_resolve_network_mode "$agent_role" "$run_dir")"
  network_args=()
  while IFS= read -r _network_flag; do
    [ -n "$_network_flag" ] || continue
    network_args+=("$_network_flag")
  done < <(sandbox_docker_network_args "$network_mode")

  local docker_run_args=(
    --name "$container_name"
    --hostname "hoca-sandbox"
    --workdir /workspace
    -v "${project_path}:/workspace"
    -v "${HOCA_ROOT}/scripts:/hoca/scripts:ro"
    -v "${HOCA_ROOT}/hoca:/hoca/hoca:ro"
    -v "${HOCA_ROOT}/templates:/hoca/templates:ro"
    -v "${sandbox_home}:/home/hoca-sandbox"
    -e "LLM_BASE_URL=${llm_url}"
    -e "LLM_MODEL=${LLM_MODEL:-ollama/qwen-14b-pro}"
    -e "LLM_API_KEY=${LLM_API_KEY:-ollama}"
    -e "HOME=/home/hoca-sandbox"
    --security-opt=no-new-privileges
    --cap-drop=ALL
    --memory=8g
    --pids-limit=512
    --user "$sandbox_user"
  )
  if [ "${#network_args[@]}" -gt 0 ]; then
    docker_run_args+=("${network_args[@]}")
  else
    docker_run_args+=(--add-host=host.docker.internal:host-gateway)
  fi

  docker run -d \
    "${docker_run_args[@]}" \
    "$SANDBOX_IMAGE" \
    sleep infinity

  echo "$container_name"
}

sandbox_exec() {
  local container_name="$1"
  shift
  docker exec -w /workspace "$container_name" "$@"
}

sandbox_stop() {
  local container_name="$1"
  docker rm -f "$container_name" >/dev/null 2>&1 || true
}

sandbox_cleanup_old() {
  # Remove containers older than 1 hour
  local containers
  containers="$(docker ps -a --filter "name=${SANDBOX_CONTAINER_PREFIX}" --format '{{.Names}}' 2>/dev/null || true)"
  for c in $containers; do
    local running
    running="$(docker inspect --format '{{.State.Running}}' "$c" 2>/dev/null || echo "false")"
    if [ "$running" = "false" ]; then
      docker rm "$c" >/dev/null 2>&1 || true
    fi
  done
}

# CLI interface
case "${1:-help}" in
  build)
    sandbox_build
    ;;
  start)
    if [ "$#" -lt 2 ]; then
      echo "Usage: sandbox-manager.sh start /path/to/project [run-id] [role] [run-dir]"
      exit 1
    fi
    sandbox_start "$2" "${3:-}" "${4:-worker}" "${5:-}"
    ;;
  exec)
    if [ "$#" -lt 3 ]; then
      echo "Usage: sandbox-manager.sh exec <container-name> <command...>"
      exit 1
    fi
    container="$2"
    shift 2
    sandbox_exec "$container" "$@"
    ;;
  stop)
    if [ "$#" -lt 2 ]; then
      echo "Usage: sandbox-manager.sh stop <container-name>"
      exit 1
    fi
    sandbox_stop "$2"
    ;;
  cleanup)
    sandbox_cleanup_old
    ;;
  help|*)
    echo "HOCA Sandbox Manager"
    echo ""
    echo "Commands:"
    echo "  build              Build the sandbox Docker image"
    echo "  start <path> [id]  Start a sandbox container for a project"
    echo "  exec <name> <cmd>  Execute a command in the sandbox"
    echo "  stop <name>        Stop and remove a sandbox container"
    echo "  cleanup            Remove stale sandbox containers"
    ;;
esac
