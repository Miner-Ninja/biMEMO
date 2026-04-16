# biMEMO

Self-hosted personal AI assistant with security-first architecture.

## Features

- **End-to-end encryption** — All data encrypted at rest with SQLCipher AES-256
- **Local LLM** — Runs locally via Ollama (no data leaves your machine)
- **Observational memory** — Automatic context compression via Dialog Recorder/Summary Distiller
- **Tool execution** — File operations, web search, with trust zone enforcement
- **Multi-channel** — Telegram bot + Discord bot + Web UI
- **Multi-process isolation** — Supervisor spawns isolated processes communicating via HMAC-authenticated Unix sockets
- **Worker isolation** — FS/Net tools run in separate workers with IPC

## Quick Start

### Prerequisites

- Docker + Docker Compose
- 8GB+ RAM
- GPU recommended (RTX 3070+) for local Ollama, or use OpenRouter API

### 1. Clone and Configure

```bash
git clone https://github.com/yourusername/bimemo.git
cd bimemo
cp .env.example .env
```

### 2. Generate Local TLS Certificate

biMEMO uses HTTPS even for local development. Generate a local self-signed
certificate before starting Docker Compose:

```bash
./scripts/generate_dev_tls.sh
```

The generated `docker/caddy/certs/cert.pem` and `docker/caddy/certs/key.pem`
files stay local and must not be committed.

### 3. Configure .env

Choose your LLM provider:

**Option A: Local Ollama (recommended with GPU)**
```env
BIMEMO_LLM_PROVIDER=ollama
BIMEMO_OLLAMA_BASE_URL=http://ollama:11434
BIMEMO_OLLAMA_MODEL=qwen3:14b
BIMEMO_OLLAMA_DIALOG_RECORDER_MODEL=qwen3:4b
```

**Option B: OpenRouter (no GPU required)**
```env
BIMEMO_LLM_PROVIDER=openrouter
BIMEMO_OPENROUTER_API_KEY=your-api-key-here
BIMEMO_OPENROUTER_MODEL=qwen/qwen3-14b
```

### 4. Start

```bash
docker-compose -f docker/docker-compose.dev.yml up -d
```

### 5. Access

- **Web UI**: http://localhost:5273
- **Backend via Caddy**: https://localhost
- **Telegram**: Start a chat with your bot (configure `BIMEMO_TELEGRAM_BOT_TOKEN` via Admin panel)
- **Discord**: Start a chat with your bot (configure Discord token via Admin panel)

### 6. First Setup

1. Open Web UI
2. Set master key (used for database encryption)
3. Start chatting!

## Architecture

biMEMO runs as a multi-process system inside a single container. The Supervisor process spawns and monitors all other processes, communicating via HMAC-authenticated Unix domain sockets.

```
┌─────────────────────────────────────────────────────────┐
│                     Supervisor                          │
│                                                         │
│  ┌──────────┐   ┌───────────┐   ┌──────────────────┐   │
│  │ DB Proxy │   │  Signing  │   │   Credential     │   │
│  │(SQLCipher)│   │   Proxy   │   │     Proxy        │   │
│  └─────┬────┘   └─────┬─────┘   └──────┬───────────┘   │
│        │  Socket A,B,C │ Socket D       │ Socket E      │
│  ┌─────▼──────────────▼───────────────▼─────────────┐   │
│  │              Orchestrator                         │   │
│  │  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │   │
│  │  │ FS Worker│  │Net Worker│  │ Dialog Recorder/│  │   │
│  │  │(in-proc) │  │(in-proc) │  │ Summary Distiller│  │   │
│  │  └──────────┘  └──────────┘  │ Re-embed loops │  │   │
│  │                              └────────────────┘  │   │
│  │  ┌──────────────┐  ┌────────────────┐            │   │
│  │  │ Telegram Bot │  │  Discord Bot   │            │   │
│  │  └──────────────┘  └────────────────┘            │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │ Socket F                      │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │              FastAPI Application                  │   │
│  │  Middleware → Routes → Gateway → SSE Streaming    │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Processes:**

| Process | Role | IPC |
|---------|------|-----|
| Supervisor | Spawns, monitors, restarts child processes | — |
| DB Proxy | SQLCipher database access (3 socket servers) | Socket A, B, C |
| Signing Proxy | JWT signing, Argon2id verification | Socket D |
| Credential Proxy | Integration credential management | Socket E |
| Orchestrator | LLM agentic loop, tool workers, bots, memory loops | Socket F (server) |
| FastAPI | HTTP API, Web UI, SSE streaming | Connects to all sockets |

## Trust Zones

Tools are classified by risk level:

| Zone | Tools | Behavior |
|------|-------|----------|
| GREEN | `file_read`, `file_search` | Executes silently |
| YELLOW | `file_write`, `web_search` | Executes, notifies after |
| RED | `email_send`, `http_fetch` | Requires user confirmation |

## Configuration

All configuration via environment variables with prefix `BIMEMO_`:

| Variable | Description |
|----------|-------------|
| `BIMEMO_DEBUG` | Enable debug mode |
| `BIMEMO_HOST` | Bind address |
| `BIMEMO_PORT` | Bind port |
| `BIMEMO_LLM_PROVIDER` | `ollama` or `openrouter` |
| `BIMEMO_KEY_SOURCE` | Key source type (currently `file`) |
| `BIMEMO_KEY_FILE_PATH` | Path to encryption key file |
| `BIMEMO_DATABASE_PATH` | Path to encrypted DB |

Bot integrations (Telegram, Discord) are configured via the Admin panel and stored in the encrypted database.

## Development

```bash
# Build and start dev environment
docker-compose -f docker/docker-compose.dev.yml up -d --build

# Run tests
docker-compose -f docker/docker-compose.dev.yml exec bimemo \
  python -m pytest tests/ -v

# Lint
docker-compose -f docker/docker-compose.dev.yml exec bimemo \
  ruff check --fix . && ruff format .
```

## Security

- **Database**: Encrypted with SQLCipher (AES-256)
- **Keys**: Master key protected by Argon2id + AES-256-GCM, stored via pluggable KeySource
- **Workers**: Isolated in-process workers with HMAC verification
- **Processes**: Separate processes for DB, signing, credentials — each with own IPC socket
- **API**: JWT authentication (Ed25519) + CSRF + rate limiting

### Secret Scanning

Run Gitleaks before publishing changes:

```bash
gitleaks detect --source . --config .gitleaks.toml --redact --verbose
```

The GitHub Actions security workflow runs the same secret scan on pushes and
pull requests.

## Project Docs

- [INSTALL.md](INSTALL.md) for installation details
- [USAGE.md](USAGE.md) for user-facing workflows
- [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull request rules
- [SECURITY.md](SECURITY.md) for private security disclosure
- [LICENSE](LICENSE) for the project license

## Tech Stack

- **Backend**: Python 3.14.4, FastAPI, Pydantic
- **Database**: SQLite + SQLCipher + sqlite-vec
- **LLM**: Ollama, OpenRouter
- **Bots**: aiogram (Telegram), discord.py (Discord)
- **Frontend**: React 19, TypeScript, Vite, Tailwind CSS
- **Container**: Docker, Caddy

## License

MIT
