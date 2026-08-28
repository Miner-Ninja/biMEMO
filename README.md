# biMEMO

**A self-hosted AI assistant with long-term memory, practical tools, and clear
control over where your data and model requests go.**

biMEMO brings web chat, Discord, persistent memory, scheduled tasks, and
workspace tools into one system you operate yourself. Use a local Ollama model
for a fully local model runtime, connect through OpenRouter, or configure
another OpenAI-compatible provider.

## Why biMEMO

- **Encrypted memory, not plaintext notes** — biMEMO's primary memory store is
  an encrypted SQLCipher database, not a folder of readable Markdown files. It
  is designed for the deeply personal context a long-running assistant can
  accumulate over time.
- **Memory that stays useful** — biMEMO turns conversations into searchable
  records and a knowledge graph of entities, relationships, and dated facts,
  then retrieves the relevant context when it is needed.
- **More than chat** — search the web, work with files and Git repositories,
  run approved commands, create reusable skills, and schedule reminders or
  background tasks (some capabilities are still being completed and refined).
- **Use the model you prefer** — choose local Ollama, OpenRouter, or a custom
  OpenAI-compatible endpoint.
- **Built around explicit boundaries** — sensitive actions require scoped or
  per-action approval, while database, signing, credential, and orchestration
  responsibilities are separated.

## What is included

- Responsive web chat with file and image input, voice transcription, chat
  history, and English/Russian UI
- Discord integration with a user allowlist; Telegram support is planned
- Long-term memory combining full-text search, vector similarity, and
  knowledge-graph retrieval
- File, web, email, workspace, Git, command, scheduling, and skill tools
- Admin pages for providers, integrations, memory, scheduled tasks, backups,
  diagnostics, and execution traces
- MCP access so external agents can search, add, and query biMEMO memory
- Docker Compose stack with Caddy, SearXNG, and faster-whisper

**Project status.** biMEMO is an independent, single-developer project in
active beta. Its core workflows are built and tested, but updates may still
introduce breaking changes, so it is not yet intended for production-critical
use. Development is moving toward a dependable, production-ready release.

## Security model

Unlike file-based memory designs, biMEMO keeps conversations, distilled memory
records, knowledge-graph entities and relationships, settings, and credentials
in SQLCipher databases. The database master key is wrapped using the
application password with Argon2id and AES-256-GCM. Internal services use
authenticated Unix sockets, and the web API adds JWT authentication, CSRF
protection, rate limiting, output redaction, and SSRF/path validation.

Tool access is policy-driven: read access can be granted for a conversation or
workspace, while writes, email, commands, Git mutations, and other sensitive
operations can require explicit approval.

Self-hosting does not automatically make every request local. OpenRouter,
custom cloud providers, web search, URL fetching, email, and messenger
integrations send the data required for those features to their configured
services. Uploaded files are stored outside the encrypted memory database and
may contain sensitive content; protect the instance storage with host or disk
encryption.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Quick start

### Requirements

- Docker with Docker Compose
- 4 GB RAM for a cloud-model setup; 8 GB or more recommended for local models
- A running Ollama instance, an OpenRouter key, or another compatible provider

```bash
git clone https://github.com/Miner-Ninja/biMEMO.git
cd biMEMO
cp .env.example .env
./scripts/generate_dev_tls.sh
docker compose -f docker/docker-compose.yml up -d --build
```

Open **https://localhost**, accept the locally generated development
certificate, create the application password, and configure an LLM provider in
**Admin → LLM**.

The development frontend is also exposed directly at
<http://localhost:5273>. Ollama is not included in the default Compose stack;
run it separately and point biMEMO to its endpoint.

For automatic unlock, platform-specific notes, and provider setup, follow
[INSTALL.md](INSTALL.md).

## How it is structured

The default Compose stack runs five services: biMEMO, the React frontend,
Caddy, SearXNG, and faster-whisper. Inside the biMEMO container, a supervisor
manages separate database, signing, credential, orchestrator, and FastAPI
processes. File-system, network, and command tools execute through dedicated
workers instead of receiving direct database or credential access.

This separation limits which component can read keys, credentials, memory, or
perform external actions; it is a trust boundary, not a substitute for host and
container hardening.

## Development

```bash
# Backend checks and tests
docker compose -f docker/docker-compose.yml exec bimemo \
  python -m pytest tests/ -q

# Frontend checks
cd app/frontend
npm ci
npm run lint
npm run build
npm run test
```

GitHub Actions also run backend tests, frontend lint/build/tests, OpenAPI drift
checks, secret scanning, dependency audits, and static security analysis.

## Community

Questions, feedback, and project discussion are welcome on the
[biMEMO Discord server](https://discord.com/invite/z5kJJeaTeS).

## Documentation

- [Installation](INSTALL.md)
- [User guide](USAGE.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Development and diagnostic scripts](scripts/README.md)

## Technology

Python 3.14 · FastAPI · React 19 · TypeScript · SQLCipher · sqlite-vec ·
Ollama · OpenRouter · Docker · Caddy

## License

[MIT](LICENSE)
