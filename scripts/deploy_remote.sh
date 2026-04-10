#!/usr/bin/env bash

set -Eeuo pipefail

DEFAULT_SSH_KEY="${HOME}/.ssh/digitalocean-terraform.priv"
DEFAULT_VM_USER="nodemaster"
DEFAULT_VM_HOST="192.168.0.88"
DEFAULT_REMOTE_PATH="/home/nodemaster/biMEMO"
DEFAULT_REPO_URL="http://192.168.0.205:3000/recoder/biMEMO.git"
DEFAULT_BRANCH="main"
DEFAULT_COMPOSE_FILE="docker/docker-compose.dev.yml"
DEFAULT_LOCAL_ENV_PATH=".env"
DEFAULT_DEPLOY_WAIT_SECONDS="180"

SSH_KEY="${SSH_KEY:-${DEFAULT_SSH_KEY}}"
VM_USER="${VM_USER:-${DEFAULT_VM_USER}}"
VM_HOST="${VM_HOST:-${DEFAULT_VM_HOST}}"
REMOTE_PATH="${REMOTE_PATH:-${DEFAULT_REMOTE_PATH}}"
REPO_URL="${REPO_URL:-${DEFAULT_REPO_URL}}"
BRANCH="${BRANCH:-${DEFAULT_BRANCH}}"
COMPOSE_FILE="${COMPOSE_FILE:-${DEFAULT_COMPOSE_FILE}}"
LOCAL_ENV_PATH="${LOCAL_ENV_PATH:-${DEFAULT_LOCAL_ENV_PATH}}"
ALLOW_DEV_REMOTE_DEPLOY="${ALLOW_DEV_REMOTE_DEPLOY:-false}"
DEPLOY_WAIT_SECONDS="${DEPLOY_WAIT_SECONDS:-${DEFAULT_DEPLOY_WAIT_SECONDS}}"

REMOTE_TARGET="${VM_USER}@${VM_HOST}"


log() {
  printf '[deploy-remote] %s\n' "$1"
}


require_command() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${command_name}" >&2
    exit 1
  fi
}


validate_remote_path() {
  if [[ -z "${REMOTE_PATH}" ]]; then
    printf 'REMOTE_PATH must not be empty\n' >&2
    exit 1
  fi

  case "${REMOTE_PATH}" in
    /|/home|/home/"${VM_USER}")
      printf 'Refusing unsafe REMOTE_PATH: %s\n' "${REMOTE_PATH}" >&2
      exit 1
      ;;
  esac
}


validate_compose_file() {
  if [[ "${COMPOSE_FILE}" == *".dev.yml" && "${ALLOW_DEV_REMOTE_DEPLOY}" != "true" ]]; then
    printf '%s\n' \
      "Refusing to deploy remote VM with dev compose file (${COMPOSE_FILE})." \
      "Set ALLOW_DEV_REMOTE_DEPLOY=true to confirm this is intentional." >&2
    exit 1
  fi
}


build_git_auth_header() {
  local credential_output
  local git_username=""
  local git_password=""

  credential_output="$(
    printf 'url=%s\n\n' "${REPO_URL}" | git credential fill
  )"

  while IFS='=' read -r key value; do
    case "${key}" in
      username)
        git_username="${value}"
        ;;
      password)
        git_password="${value}"
        ;;
    esac
  done <<< "${credential_output}"

  if [[ -z "${git_username}" || -z "${git_password}" ]]; then
    printf 'Could not resolve git credentials for %s\n' "${REPO_URL}" >&2
    exit 1
  fi

  printf 'Authorization: Basic %s' "$(
    printf '%s' "${git_username}:${git_password}" | base64 | tr -d '\n'
  )"
}


main() {
  require_command "ssh"
  require_command "git"
  require_command "base64"
  require_command "scp"
  validate_remote_path
  validate_compose_file

  if [[ ! -f "${SSH_KEY}" ]]; then
    printf 'SSH key not found: %s\n' "${SSH_KEY}" >&2
    exit 1
  fi

  if [[ ! -f "${LOCAL_ENV_PATH}" ]]; then
    printf 'Local .env file not found: %s\n' "${LOCAL_ENV_PATH}" >&2
    exit 1
  fi

  local git_auth_header
  git_auth_header="$(build_git_auth_header)"

log "Running remote deployment on ${REMOTE_TARGET}"

  ssh -i "${SSH_KEY}" "${REMOTE_TARGET}" "bash -s" <<EOF
set -Eeuo pipefail

log() {
  printf '[remote-deploy] %s\n' "\$1"
}

require_command() {
  local command_name="\$1"

  if ! command -v "\${command_name}" >/dev/null 2>&1; then
    printf 'Missing required command on remote host: %s\n' "\${command_name}" >&2
    exit 1
  fi
}

log "Checking required commands"
require_command "git"
require_command "docker"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  printf 'Missing required compose command on remote host: docker compose or docker-compose\n' >&2
  exit 1
fi

mkdir -p "$(dirname "${REMOTE_PATH}")"

if [[ -e "${REMOTE_PATH}" && ! -d "${REMOTE_PATH}/.git" ]]; then
  printf 'Remote path exists but is not a git checkout: %s\n' "${REMOTE_PATH}" >&2
  exit 1
fi

if [[ ! -d "${REMOTE_PATH}/.git" ]]; then
  log "Cloning repository into ${REMOTE_PATH}"
  git -c http.extraHeader='${git_auth_header}' clone "${REPO_URL}" "${REMOTE_PATH}"
fi

cd "${REMOTE_PATH}"

log "Fetching ${BRANCH}"
git -c http.extraHeader='${git_auth_header}' fetch origin "${BRANCH}"

log "Resetting working tree to origin/${BRANCH}"
git reset --hard "origin/${BRANCH}"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  printf 'Missing compose file: %s\n' "${COMPOSE_FILE}" >&2
  exit 1
fi
EOF

  log "Uploading local .env to ${REMOTE_TARGET}:${REMOTE_PATH}/.env"
  scp -i "${SSH_KEY}" "${LOCAL_ENV_PATH}" "${REMOTE_TARGET}:${REMOTE_PATH}/.env"

ssh -i "${SSH_KEY}" "${REMOTE_TARGET}" "bash -s" <<EOF
set -Eeuo pipefail

log() {
  printf '[remote-deploy] %s\n' "\$1"
}

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  printf 'Missing required compose command on remote host: docker compose or docker-compose\n' >&2
  exit 1
fi

cd "${REMOTE_PATH}"

if [[ ! -f ".env" ]]; then
  printf 'Missing required .env file in %s\n' "${REMOTE_PATH}" >&2
  exit 1
fi

log "Building and starting the stack"
"\${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" up -d --build

log "Container status"
"\${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" ps

log "Waiting for bimemo health endpoint"
deadline=\$((SECONDS + ${DEPLOY_WAIT_SECONDS}))
until "\${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" exec -T bimemo python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" >/dev/null 2>&1
do
  if (( SECONDS >= deadline )); then
    printf 'Timed out waiting for bimemo health endpoint after %s seconds\n' "${DEPLOY_WAIT_SECONDS}" >&2
    "\${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" ps >&2
    exit 1
  fi
  sleep 5
done

log "Health check passed"
"\${COMPOSE_CMD[@]}" -f "${COMPOSE_FILE}" exec -T bimemo python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
EOF
}


main "\$@"
