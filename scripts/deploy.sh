#!/usr/bin/env bash

set -Eeuo pipefail

DEFAULT_REMOTE="origin"
DEFAULT_BRANCH="main"
DEFAULT_COMPOSE_FILE="docker/docker-compose.dev.yml"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

REMOTE="${1:-${DEFAULT_REMOTE}}"
BRANCH="${2:-${DEFAULT_BRANCH}}"
COMPOSE_FILE="${COMPOSE_FILE:-${DEFAULT_COMPOSE_FILE}}"
ALLOW_DEV_DEPLOY="${ALLOW_DEV_DEPLOY:-false}"


log() {
  printf '[deploy] %s\n' "$1"
}


require_command() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${command_name}" >&2
    exit 1
  fi
}


validate_compose_file() {
  if [[ "${COMPOSE_FILE}" == *".dev.yml" && "${ALLOW_DEV_DEPLOY}" != "true" ]]; then
    printf '%s\n' \
      "deploy.sh targets ${COMPOSE_FILE}, which is a development compose file." \
      "Set ALLOW_DEV_DEPLOY=true to confirm this is intentional." >&2
    exit 1
  fi
}


main() {
  local compose_cmd=()

  require_command "git"
  validate_compose_file

  if docker compose version >/dev/null 2>&1; then
    compose_cmd=(docker compose)
  else
    require_command "docker-compose"
    compose_cmd=(docker-compose)
  fi

  cd "${REPO_DIR}"

  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    printf 'Compose file not found: %s\n' "${COMPOSE_FILE}" >&2
    exit 1
  fi

  log "Fetching ${REMOTE}/${BRANCH}"
  git fetch "${REMOTE}" "${BRANCH}"

  log "Resetting working tree to ${REMOTE}/${BRANCH}"
  git reset --hard "${REMOTE}/${BRANCH}"

  log "Starting containers from ${COMPOSE_FILE}"
  "${compose_cmd[@]}" -f "${COMPOSE_FILE}" up -d --build

  log "Current container status"
  "${compose_cmd[@]}" -f "${COMPOSE_FILE}" ps
}


main "$@"
