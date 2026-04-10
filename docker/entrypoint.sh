#!/bin/sh
set -e

# Auto-generate master key for database encryption if it doesn't exist.
# The key is 32 bytes of random data with 0600 permissions.
# WARNING: Losing this key means losing all encrypted data.
KEY_PATH="${BIMEMO_KEY_FILE_PATH:-/data/keyfile/master.key}"
if [ ! -f "$KEY_PATH" ]; then
    echo "Master key not found at $KEY_PATH — generating new key..."
    python -c "import os; open('$KEY_PATH', 'wb').write(os.urandom(32))"
    chmod 600 "$KEY_PATH"
    echo "Master key generated. Back up $KEY_PATH — losing it means losing all data."
fi

# Generate Ollama Bearer token and write to shared tmpfs volume.
# Caddy (read-only) and bimemo (read-write) share /run/ollama-token.
# Skip if volume is not writable (e.g. OpenRouter provider, no Ollama).
python -c "import secrets; print(secrets.token_hex(32))" > /run/ollama-token/token 2>/dev/null || true

exec "$@"
