#!/usr/bin/env sh
set -eu

# NOTE: docker-compose mounts docker/caddy/certs into the Caddy container.
# The CA private key must NOT live there, or a compromised web-facing proxy
# could mint trusted certificates. Keep the CA in a sibling dir that is not
# mounted into any container; only cert.pem/key.pem go under certs/.
CERT_DIR="docker/caddy/certs"
CA_DIR="docker/caddy/ca"
CERT_FILE="${CERT_DIR}/cert.pem"
KEY_FILE="${CERT_DIR}/key.pem"
CA_KEY="${CA_DIR}/ca-key.pem"
CA_CERT="${CA_DIR}/ca-cert.pem"
CSR_FILE="${CERT_DIR}/cert.csr"

mkdir -p "${CERT_DIR}" "${CA_DIR}"

# ── Idempotent guard: skip if everything already exists ──────────────
if [ -f "${CERT_FILE}" ] && [ -f "${KEY_FILE}" ] && [ -f "${CA_CERT}" ]; then
  echo "TLS certificate and CA already exist:"
  echo "  ${CERT_FILE}"
  echo "  ${KEY_FILE}"
  echo "  ${CA_CERT}"
  exit 0
fi

# ── Step 1: create CA key + self-signed CA cert (if not present) ────
if [ ! -f "${CA_KEY}" ] || [ ! -f "${CA_CERT}" ]; then
  echo "Generating local CA..."
  openssl genrsa -out "${CA_KEY}" 4096 2>/dev/null
  openssl req -x509 -new -nodes \
    -key "${CA_KEY}" \
    -out "${CA_CERT}" \
    -days 3650 \
    -subj "/CN=biMEMO Local CA" \
    2>/dev/null
  chmod 600 "${CA_KEY}"
  chmod 644 "${CA_CERT}"
  echo "  CA key:  ${CA_KEY}"
  echo "  CA cert: ${CA_CERT}"
fi

# ── Step 2: collect SAN names ────────────────────────────────────────
# Always include: localhost, 127.0.0.1
# Auto-detect: LAN IPs (non-loopback), hostname
SAN_NAMES="DNS:localhost,IP:127.0.0.1"

# Detect LAN IPs (exclude 127.0.0.0/8)
# Use set +e to tolerate failures from ip/grep on minimal environments
set +e
LAN_IPS=$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -v '^127\.' || true)
set -eu
for ip in ${LAN_IPS}; do
  SAN_NAMES="${SAN_NAMES},IP:${ip}"
done

# Add hostname
set +e
HOSTNAME=$(hostname 2>/dev/null || echo "bimemo")
set -eu
if ! echo "${SAN_NAMES}" | grep -q "DNS:${HOSTNAME}"; then
  SAN_NAMES="${SAN_NAMES},DNS:${HOSTNAME}"
fi

# Allow operator to append names via BIMEMO_TLS_EXTRA_SAN
EXTRA_SAN="${BIMEMO_TLS_EXTRA_SAN:-}"
if [ -n "${EXTRA_SAN}" ]; then
  SAN_NAMES="${SAN_NAMES},${EXTRA_SAN}"
fi

echo "SAN entries: ${SAN_NAMES}"

# ── Step 3: generate server key + CSR ────────────────────────────────
openssl genrsa -out "${KEY_FILE}" 4096 2>/dev/null

openssl req -new \
  -key "${KEY_FILE}" \
  -out "${CSR_FILE}" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=${SAN_NAMES}" \
  2>/dev/null

# ── Step 4: sign with the CA ─────────────────────────────────────────
openssl x509 -req \
  -in "${CSR_FILE}" \
  -CA "${CA_CERT}" \
  -CAkey "${CA_KEY}" \
  -CAcreateserial \
  -out "${CERT_FILE}" \
  -days 365 \
  -copy_extensions copyall \
  2>/dev/null

# Clean up CSR
rm -f "${CSR_FILE}"

chmod 600 "${KEY_FILE}"
chmod 644 "${CERT_FILE}"

echo "Generated TLS certificate signed by local CA:"
echo "  cert: ${CERT_FILE}"
echo "  key:  ${KEY_FILE}"
echo "  CA (public):  ${CA_CERT}"
echo "  CA key (host-only, keep out of containers): ${CA_KEY}"
echo ""
echo "Point BIMEMO_CA_ROOT_PATH at the PUBLIC CA cert (${CA_CERT}) so the"
echo "admin connection page can offer it for download."
echo "Trust the CA root on client machines to connect without errors."
