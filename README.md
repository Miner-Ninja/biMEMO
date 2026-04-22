# biMEMO

Self-hosted personal AI assistant with encrypted storage, local-first runtime,
and isolated tool execution.

biMEMO is built for people who want their own assistant stack instead of a
hosted black box. It combines a web chat, messenger integrations, encrypted
memory, local or remote LLM providers, and strict execution boundaries inside a
single self-hosted system.

## Why biMEMO

- **Private by design** — encrypted local storage and self-hosted deployment
- **Provider-flexible** — run with local Ollama or a remote provider
- **Operationally explicit** — multi-process architecture with clear trust
  boundaries
- **Useful beyond web chat** — Telegram, Discord, memory tools, and admin UI in
  one stack

## What You Get

- personal assistant web UI,
- encrypted memory and diagnostics,
- local HTTPS for development,
- Telegram and Discord integrations,
- security scanning in CI before public release.

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

If you want the full installation guide instead of the short path below, see
[INSTALL.md](INSTALL.md).

### 1. Clone and Configure

```bash
git clone https://github.com/<owner>/biMEMO.git
cd biMEMO
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

Review `.env` before starting the stack. The default development compose file
starts the application, frontend, Caddy, SearXNG, and speech-to-text service.
It uses manual unlock by default: you enter the application password in the
browser when setup or unlock is required.

LLM providers can be configured after first login in the Admin UI at
`/admin/llm`. If you prefer environment-based setup, uncomment the matching
`BIMEMO_LLM_PROVIDER` block in `.env`; these values are copied into the
encrypted database on startup.

### 4. Start

```bash
docker-compose -f docker/docker-compose.yml up -d
```

### 5. Access

- **Web UI**: http://localhost:5273
- **Backend via Caddy**: https://localhost
- **Telegram**: Configure a Telegram integration in the Admin panel, then start a chat with your bot
- **Discord**: Configure a Discord integration in the Admin panel, then start a chat with your bot

### 6. First Setup

1. Open the Web UI
2. Create the first application password
3. Configure an LLM provider in the Admin UI
4. Start chatting

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
| `BIMEMO_KEY_SOURCE` | Key source type (`password` for public installs) |
| `BIMEMO_ENCRYPTED_KEY_FILE_PATH` | Path to encrypted master-key file |
| `BIMEMO_AUTOMATIC_UNLOCK` | `false` for manual unlock, `true` for automatic unlock |
| `BIMEMO_UNLOCK_PASSWORD_FILE` | Automatic unlock password secret file |
| `BIMEMO_DATABASE_PATH` | Path to encrypted DB |

Bot integrations (Telegram, Discord) are configured via the Admin panel and stored in the encrypted database.

## Development

```bash
# Build and start dev environment
docker-compose -f docker/docker-compose.yml up -d --build

# Run tests
docker-compose -f docker/docker-compose.yml exec bimemo \
  python -m pytest tests/ -v

# Lint
docker-compose -f docker/docker-compose.yml exec bimemo \
  ruff check --fix . && ruff format .
```

## Security

- **Database**: Encrypted with SQLCipher (AES-256)
- **Keys**: Master key encrypted by the application password with Argon2id + AES-256-GCM
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

- [docs/README.md](docs/README.md) for the technical document index
- [INSTALL.md](INSTALL.md) for installation details
- [USAGE.md](USAGE.md) for user-facing workflows
- [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull request rules
- [SECURITY.md](SECURITY.md) for private security disclosure
- [scripts/README.md](scripts/README.md) for local diagnostic helper scripts
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
