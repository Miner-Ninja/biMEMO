#!/usr/bin/env sh
set -eu

CERT_DIR="docker/caddy/certs"
CERT_FILE="${CERT_DIR}/cert.pem"
KEY_FILE="${CERT_DIR}/key.pem"

mkdir -p "${CERT_DIR}"

if [ -f "${CERT_FILE}" ] && [ -f "${KEY_FILE}" ]; then
  echo "Local TLS certificate already exists:"
  echo "  ${CERT_FILE}"
  echo "  ${KEY_FILE}"
  exit 0
fi

openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout "${KEY_FILE}" \
  -out "${CERT_FILE}" \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "${KEY_FILE}"
chmod 644 "${CERT_FILE}"

echo "Generated local TLS certificate:"
echo "  ${CERT_FILE}"
echo "  ${KEY_FILE}"
