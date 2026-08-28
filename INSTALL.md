# biMEMO Installation Guide

## System Requirements

### Minimum
- 4GB RAM
- CPU-only (via OpenRouter API)

### Recommended
- 8GB+ RAM
- GPU with 6GB+ VRAM (RTX 3060, RTX 3070+)
- 20GB disk space

### Supported Platforms
- Linux (recommended)
- macOS (limited GPU support)
- Windows via WSL2

---

## Installation (Docker Compose)

### 1. Clone Repository

```bash
git clone https://github.com/Miner-Ninja/biMEMO.git
cd biMEMO
```

### 2. Configure Environment

```bash
cp .env.example .env
nano .env
```

Required base variables:

```env
# Core
BIMEMO_DEBUG=false
BIMEMO_KEY_SOURCE=password
BIMEMO_ENCRYPTED_KEY_FILE_PATH=/data/keyfile/master.key.enc
BIMEMO_AUTOMATIC_UNLOCK=false
BIMEMO_DATABASE_PATH=/data/db/bimemo.db

# Optional LLM provider seed
# BIMEMO_LLM_PROVIDER=openrouter
```

LLM providers can also be configured after first login in the Admin UI at
`/admin/llm`. Environment values are useful for repeatable local setups; Admin
UI values are stored encrypted in the database.

### 3. Generate Local TLS Certificate

biMEMO uses HTTPS even for local development. The repository does not include
TLS private keys, so generate a local self-signed certificate after cloning:

```bash
./scripts/generate_dev_tls.sh
```

This creates:

```text
docker/caddy/certs/cert.pem
docker/caddy/certs/key.pem
```

These files are ignored by git and must stay local. If you need a fresh local
certificate, delete both files and run the script again.

### 4. Application Password and Database Encryption

biMEMO uses one password for both database startup and Web UI login.
The raw SQLCipher master key is not configured in public installs.

The default mode is manual unlock: a person enters the application password in
the browser when setup or unlock is required. No startup password is stored on
disk for this mode.

For automatic unlock, Docker provides the same application password from a
local secret file. Create the file before the first start:

```bash
mkdir -p secrets
printf '%s' 'replace-with-a-strong-password' > secrets/bimemo_unlock_password
chmod 600 secrets/bimemo_unlock_password
```

The `secrets/` directory is ignored by git and must stay local. The secret file
is plaintext: anyone who can read it can unlock the encrypted database.

Then enable automatic unlock in `.env`:

```env
BIMEMO_AUTOMATIC_UNLOCK=true
BIMEMO_UNLOCK_PASSWORD_FILE=/run/secrets/bimemo_unlock_password
```

On first successful startup, biMEMO creates `/data/keyfile/master.key.enc` in
the `keyfile-data` Docker volume. Back up this encrypted key file together with
the encrypted database volume. Losing either one makes existing data unreadable.

To switch from manual unlock to automatic unlock, create
`secrets/bimemo_unlock_password` with the same application password, set
`BIMEMO_AUTOMATIC_UNLOCK=true`, set
`BIMEMO_UNLOCK_PASSWORD_FILE=/run/secrets/bimemo_unlock_password`, and restart
the stack.

To switch from automatic unlock back to manual unlock, stop the stack and start
again with `BIMEMO_AUTOMATIC_UNLOCK=false`. Keep the encrypted key file and
database volumes unchanged. The Web UI will ask for the same application
password on the next start.

### 5. Start Services

```bash
docker-compose -f docker/docker-compose.yml up -d
```

### 6. Verify

```bash
docker-compose -f docker/docker-compose.yml ps
```

---

## LLM Provider Setup

### Option A: Ollama (Local)

The default development compose file does not start Ollama for you. Run Ollama
on the host or add your own Ollama service, then point biMEMO to that endpoint.

Pull a model in your Ollama runtime:
```bash
ollama pull qwen3:14b
```

Configure via Admin UI, or seed through `.env`:
```env
BIMEMO_LLM_PROVIDER=ollama
BIMEMO_OLLAMA_BASE_URL=http://host.docker.internal:11434
BIMEMO_OLLAMA_MODEL=qwen3:14b
BIMEMO_OLLAMA_DIALOG_RECORDER_MODEL=qwen3:4b
```

On Linux, `host.docker.internal` may require an extra Docker host mapping.
If you add Ollama as a service in the compose file, use that service name
instead.

### Option B: OpenRouter (Cloud)

1. Get API key: https://openrouter.ai/keys

2. Configure via Admin UI, or seed through `.env`:
```env
BIMEMO_LLM_PROVIDER=openrouter
BIMEMO_OPENROUTER_API_KEY=<your-openrouter-api-key>
BIMEMO_OPENROUTER_MODEL=qwen/qwen3-14b
BIMEMO_OPENROUTER_DIALOG_RECORDER_MODEL=qwen/qwen3-4b
```

---

## Telegram Bot

Telegram support is planned but is not available in the current beta. Discord
is the supported messenger integration for now.

---

## Discord Bot Setup

Bot tokens are configured via the Admin panel (stored encrypted in the database).

1. Go to https://discord.com/developers/applications
2. Create a new application
3. Go to Bot tab → create bot → copy token
4. Enable **Message Content Intent** under Privileged Gateway Intents
5. Go to OAuth2 → URL Generator → select `bot` scope with `Send Messages` + `Read Message History` permissions
6. Use the generated URL to invite the bot to your server (or use DMs directly)
7. Get your Discord user ID (enable Developer Mode in Discord settings, right-click your name → Copy User ID)
8. Open Admin panel → Integrations → Create
9. Add Discord integration with your bot token and allowed user IDs

---

## Web Search Setup (Optional)

For web search functionality, SearXNG is included in docker/docker-compose.yml:

```env
BIMEMO_SEARXNG_URL=http://searxng:8080/search
```

---

## First Run Setup

1. Start services: `docker-compose -f docker/docker-compose.yml up -d`
2. Open http://localhost:5273
3. Open https://localhost if you need to test the backend through Caddy TLS
4. You'll see the setup page
5. Create the first application password when the setup screen appears
6. The database is created and encrypted automatically

---

## Development Setup

```bash
docker-compose -f docker/docker-compose.yml up -d --build
```

This starts biMEMO with:
- Source code bind-mounts for hot reload (`app/backend/`, `tests/`)
- Debug mode enabled
- Frontend exposed on `127.0.0.1:5273`
- No Ollama dependency (use OpenRouter for dev)

Run tests:
```bash
docker-compose -f docker/docker-compose.yml exec bimemo \
  python -m pytest tests/ -v
```

---

## Troubleshooting

### "Encrypted key file not found" or "Health check timeout for db-proxy"

The encrypted key file is created on first successful password startup:

```bash
docker-compose -f docker/docker-compose.yml exec bimemo \
  test -f /data/keyfile/master.key.enc
```

If the encrypted key file or database volume was lost, restore both from the
same backup set. A new encrypted key file cannot open an old database.

If you are intentionally starting fresh, remove the database and key volumes
together, then run setup again with a new password.

### "Connection refused" errors

```bash
# Check all services
docker-compose -f docker/docker-compose.yml ps

# View logs
docker-compose -f docker/docker-compose.yml logs bimemo
docker-compose -f docker/docker-compose.yml logs caddy
```

### Out of memory

- Use smaller model: `qwen3:4b` instead of `qwen3:14b`
- Use OpenRouter instead of local Ollama

### Database locked

```bash
# Stop all services
docker-compose -f docker/docker-compose.yml down

# Restart
docker-compose -f docker/docker-compose.yml up -d
```

### SSL certificate errors (dev)

Generate or refresh the local development certificate:

```bash
rm -f docker/caddy/certs/cert.pem docker/caddy/certs/key.pem
./scripts/generate_dev_tls.sh
docker-compose -f docker/docker-compose.yml restart caddy
```

Browsers warn about self-signed local certificates. This is expected for local
development unless you install your own trusted local certificate authority.

---

## Security Notes

1. **Manual unlock** — Manual mode stores no application password on disk
2. **Automatic unlock** — The Docker secret file is plaintext; keep it private
3. **API keys** — Never commit to git
4. **Firewall** — Only expose ports you need
5. **Backups** — Back up the encrypted database and encrypted key file together

---

## Next Steps

- Read [USAGE.md](USAGE.md) for user guide
- See [README.md](README.md) for the architecture overview
- See [scripts/README.md](scripts/README.md) for diagnostic helper scripts
