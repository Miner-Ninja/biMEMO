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
git clone https://github.com/<owner>/biMEMO.git
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
BIMEMO_KEY_SOURCE=file
BIMEMO_KEY_FILE_PATH=/data/keyfile/master.key
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

### 4. Master Key (Database Encryption)

A 32-byte master key is required to encrypt the SQLCipher database.
On first start, the entrypoint **auto-generates** the key at `/data/keyfile/master.key` inside the container (stored in the `keyfile-data` Docker volume).

To generate manually (e.g. for a pre-existing volume):

```bash
docker-compose -f docker/docker-compose.dev.yml run --rm bimemo \
  sh -c 'python -c "import os; open(\"/data/keyfile/master.key\", \"wb\").write(os.urandom(32))" && chmod 600 /data/keyfile/master.key'
```

> **WARNING**: Back up this key. Losing it means losing all encrypted data.
> The key lives in the `keyfile-data` Docker volume. To back up:
> ```bash
> docker-compose -f docker/docker-compose.dev.yml cp bimemo:/data/keyfile/master.key ./master.key.backup
> ```

### 5. Start Services

```bash
docker-compose -f docker/docker-compose.dev.yml up -d
```

### 6. Verify

```bash
docker-compose -f docker/docker-compose.dev.yml ps
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

## Telegram Bot Setup

Bot tokens are configured via the Admin panel (stored encrypted in the database).

1. Create bot via @BotFather on Telegram
2. Get bot token
3. Start chat with bot
4. Get your user ID (use @userinfobot)
5. Open Admin panel → Integrations → Create
6. Add Telegram integration with your bot token and allowed user IDs

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

For web search functionality, SearXNG is included in docker/docker-compose.dev.yml:

```env
BIMEMO_SEARXNG_URL=http://searxng:8888
```

---

## First Run Setup

1. Start services: `docker-compose -f docker/docker-compose.dev.yml up -d`
2. Open http://localhost:5273
3. Open https://localhost if you need to test the backend through Caddy TLS
4. You'll see the setup page
5. Get the setup token from logs: `docker-compose -f docker/docker-compose.dev.yml logs bimemo | grep "Setup token"`
6. Enter setup token and create a master password (16+ characters)
7. Database is created and encrypted automatically

---

## Development Setup

```bash
docker-compose -f docker/docker-compose.dev.yml up -d --build
```

This starts biMEMO with:
- Source code bind-mounts for hot reload (`app/backend/`, `tests/`)
- Debug mode enabled
- Frontend exposed on `127.0.0.1:5273`
- No Ollama dependency (use OpenRouter for dev)

Run tests:
```bash
docker-compose -f docker/docker-compose.dev.yml exec bimemo \
  python -m pytest tests/ -v
```

---

## Troubleshooting

### "Key file not found" or "Health check timeout for db-proxy"

The master key is auto-generated on first start. If the volume was reset or the key was lost:

```bash
docker-compose -f docker/docker-compose.dev.yml run --rm bimemo \
  sh -c 'python -c "import os; open(\"/data/keyfile/master.key\", \"wb\").write(os.urandom(32))" && chmod 600 /data/keyfile/master.key'
docker-compose -f docker/docker-compose.dev.yml restart bimemo
```

> Note: A new key means old encrypted data is unreadable. Delete the database if starting fresh:
> ```bash
> docker-compose -f docker/docker-compose.dev.yml exec bimemo rm -f /data/db/bimemo.db
> docker-compose -f docker/docker-compose.dev.yml restart bimemo
> ```

### "Connection refused" errors

```bash
# Check all services
docker-compose -f docker/docker-compose.dev.yml ps

# View logs
docker-compose -f docker/docker-compose.dev.yml logs bimemo
docker-compose -f docker/docker-compose.dev.yml logs caddy
```

### Out of memory

- Use smaller model: `qwen3:4b` instead of `qwen3:14b`
- Use OpenRouter instead of local Ollama

### Database locked

```bash
# Stop all services
docker-compose -f docker/docker-compose.dev.yml down

# Restart
docker-compose -f docker/docker-compose.dev.yml up -d
```

### SSL certificate errors (dev)

Generate or refresh the local development certificate:

```bash
rm -f docker/caddy/certs/cert.pem docker/caddy/certs/key.pem
./scripts/generate_dev_tls.sh
docker-compose -f docker/docker-compose.dev.yml restart caddy
```

Browsers warn about self-signed local certificates. This is expected for local
development unless you install your own trusted local certificate authority.

---

## Security Notes

1. **Master key** — Keep safe! Without it, data is lost forever
2. **API keys** — Never commit to git
3. **Firewall** — Only expose ports you need
4. **Updates** — Keep dependencies updated
5. **Backups** — Regularly backup encrypted database via Admin panel

---

## Next Steps

- Read [USAGE.md](USAGE.md) for user guide
- See [docs/SPECIFICATION.md](docs/SPECIFICATION.md) for technical specification
- See [docs/DATA_FLOWS.md](docs/DATA_FLOWS.md) for data flow details
