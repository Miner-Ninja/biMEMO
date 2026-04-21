# Contributing

Thanks for considering a contribution to biMEMO.

## Before You Start

- Read [README.md](README.md) for the project overview.
- Read [INSTALL.md](INSTALL.md) for local setup.
- Use Docker for development. This repository does not use a local Python
  virtual environment for the main workflow.

## Local Setup

```bash
git clone https://github.com/<owner>/biMEMO.git
cd biMEMO
cp .env.example .env
./scripts/generate_dev_tls.sh
docker-compose -f docker/docker-compose.dev.yml up -d --build
```

## Development Rules

- Keep changes small and focused.
- Preserve existing user-visible behavior unless the change intentionally
  updates it.
- Prefer simple code over compatibility wrappers and temporary layers.
- Add or update tests when behavior changes.
- Do not commit secrets, local TLS files, or `.env` values.

## Tests and Checks

Run checks from the repository root.

```bash
docker-compose -f docker/docker-compose.dev.yml exec bimemo ruff check --fix .
docker-compose -f docker/docker-compose.dev.yml exec bimemo ruff format .
docker-compose -f docker/docker-compose.dev.yml exec bimemo python -m pytest tests/ -v
docker run --rm -v "$PWD:/repo" -w /repo ghcr.io/gitleaks/gitleaks:latest \
  detect --source . --config .gitleaks.toml --redact --verbose
```

## Pull Requests

- Explain what was wrong before the change.
- Explain what you changed.
- Explain the practical effect of the change.
- Mention test coverage for the change.
- Keep refactors separate from unrelated feature work whenever possible.

## Security Reports

For security issues, do not open a public issue. Follow the process in
[SECURITY.md](SECURITY.md).
