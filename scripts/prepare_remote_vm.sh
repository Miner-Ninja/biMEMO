#!/usr/bin/env bash

set -Eeuo pipefail

DEFAULT_SSH_KEY="${HOME}/.ssh/digitalocean-terraform.priv"
DEFAULT_VM_USER="nodemaster"
DEFAULT_VM_HOST="192.168.0.88"
DEFAULT_REMOTE_PATH="/home/nodemaster/biMEMO"

SSH_KEY="${SSH_KEY:-${DEFAULT_SSH_KEY}}"
VM_USER="${VM_USER:-${DEFAULT_VM_USER}}"
VM_HOST="${VM_HOST:-${DEFAULT_VM_HOST}}"
REMOTE_PATH="${REMOTE_PATH:-${DEFAULT_REMOTE_PATH}}"

REMOTE_TARGET="${VM_USER}@${VM_HOST}"


log() {
  printf '[prepare-remote-vm] %s\n' "$1"
}


require_command() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${command_name}" >&2
    exit 1
  fi
}


main() {
  require_command "ssh"

  if [[ ! -f "${SSH_KEY}" ]]; then
    printf 'SSH key not found: %s\n' "${SSH_KEY}" >&2
    exit 1
  fi

  log "Preparing ${REMOTE_TARGET}"

  ssh -i "${SSH_KEY}" "${REMOTE_TARGET}" "bash -s" <<EOF
set -Eeuo pipefail

log() {
  printf '[remote-vm] %s\n' "\$1"
}

log "Refreshing apt index"
sudo apt-get update

log "Installing required packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  git \
  docker.io \
  docker-compose

if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
  log "Installing docker-compose-v2"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
fi

log "Enabling Docker service"
sudo systemctl enable --now docker

log "Adding ${VM_USER} to the docker group"
sudo usermod -aG docker "${VM_USER}"

log "Creating deployment directory"
mkdir -p "${REMOTE_PATH}"

log "Verifying installed commands"
docker --version
if docker compose version >/dev/null 2>&1; then
  docker compose version
else
  docker-compose --version
fi
git --version

log "VM preparation complete"
printf '[remote-vm] NOTE: log out and reconnect once so new docker group membership is applied cleanly.\n'
EOF
}


main "\$@"
