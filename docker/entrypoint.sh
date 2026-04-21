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

# Generate an Ollama Bearer token only when the optional shared token
# directory exists. Dev and test containers can run without this mount.
if [ -d /run/ollama-token ] && [ -w /run/ollama-token ]; then
    python -c "import secrets; print(secrets.token_hex(32))" > /run/ollama-token/token
fi

exec "$@"
