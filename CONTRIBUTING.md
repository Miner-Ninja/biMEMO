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
docker-compose -f docker/docker-compose.yml up -d --build
```

## Development Rules

- Keep changes small and focused.
- Preserve existing user-visible behavior unless the change intentionally
  updates it.
- Prefer simple code over compatibility wrappers and temporary layers.
- Add or update tests when behavior changes.
- Do not commit secrets, local TLS files, or `.env` values.

## Tests and Checks

Run the full pre-merge gate from the repository root:

```bash
./scripts/verify.py
```

It runs, in order: `image` (rebuild the bimemo image from the current
tree), `python` (ruff check + ruff format --check + pytest in a
disposable container that checks the current tree), `frontend` (npm ci
+ lint + build + unit tests in a disposable container from the image's
frontend build stage), and `security` (gitleaks secret scan). Nothing is
written to the host tree and no running stack is restarted. The stages
mirror the GitHub CI workflows, so a green run means a green CI after
the push. Run a single stage with, for example,
`./scripts/verify.sh frontend`.

Individual checks, if you only need one (these apply fixes and
formatting rather than only checking). The ruff checks need the bimemo
image built first (`./scripts/verify.py image`); the frontend check
needs the development stack running:

```bash
docker run --rm -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo/app \
  docker-bimemo:latest ruff check --fix app/backend tests scripts
docker run --rm -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo/app \
  docker-bimemo:latest ruff format app/backend tests scripts
docker-compose -f docker/docker-compose.yml exec bimemo python -m pytest tests/ -v
docker-compose -f docker/docker-compose.yml exec -T frontend sh -c \
  "npm ci && npm run lint && npm run build && npm run test"
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
