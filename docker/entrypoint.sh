#!/bin/sh
set -e

# Auto-generate a raw master key only for the legacy file mode.
# Password mode creates / unlocks an encrypted key file inside the app.
KEY_SOURCE="${BIMEMO_KEY_SOURCE:-password}"
if [ "$KEY_SOURCE" = "file" ]; then
    KEY_PATH="${BIMEMO_KEY_FILE_PATH:-/data/keyfile/master.key}"
    if [ ! -f "$KEY_PATH" ]; then
        echo "Master key not found at $KEY_PATH — generating new key..."
        python -c "import os; open('$KEY_PATH', 'wb').write(os.urandom(32))"
        chmod 600 "$KEY_PATH"
        echo "Master key generated. Back up $KEY_PATH — losing it means losing all data."
    fi
fi

# Generate an Ollama Bearer token only when the optional shared token
# directory exists. Dev and test containers can run without this mount.
if [ -d /run/ollama-token ] && [ -w /run/ollama-token ]; then
    python -c "import secrets; print(secrets.token_hex(32))" > /run/ollama-token/token
fi

exec "$@"
