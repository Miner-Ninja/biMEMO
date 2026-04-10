# Python Code Rules

> Python 3.14.4 · PEP 8 · Ruff 0.15+ · Docker + Docker Compose
> This file is for AI coding agents. Symlink as needed:
> `ln -s CODE_RULES.md CLAUDE.md` or `ln -s CODE_RULES.md AGENTS.md`
>
> **ALL development, testing, and running happens inside Docker containers.** No local Python, no venv — only Docker.
> **Ruff is the ONLY linter and formatter in this project.** No Black, no Flake8, no isort, no Pylint — only Ruff.
> When this document says "lint" it means `ruff check`. When it says "format" it means `ruff format`.
>
> **ALL code, comments, docstrings, commit messages, variable names, and repository documentation — ENGLISH ONLY.** No exceptions.
> User-facing chat explanations are governed separately by `AGENTS.md`.
>
> **Code MUST be as simple as possible.** No unnecessary abstractions, no over-engineering, no convoluted patterns. Prefer straightforward, readable solutions over clever or complex ones. If a simpler approach exists — use it.
>
> **ALWAYS use `docker-compose` (hyphenated) instead of `docker compose` (space-separated).** In all commands, documentation, and scripts.

## Commands

```bash
# --- Docker (primary way to run everything) ---
cd docker
docker compose up -d                      # Start all services (production)
docker compose -f docker-compose.yml \
  -f docker-compose.dev.yml up -d \
  --build bimemo                       # Start dev environment
docker compose down                       # Stop all services
docker compose logs -f bimemo          # Follow service logs
docker compose exec bimemo bash        # Shell into container

# --- Lint & format (run inside container) ---
docker compose exec bimemo ruff check --fix .
docker compose exec bimemo ruff format .
docker compose exec bimemo ruff check .

# --- Tests (run inside container) ---
docker compose exec bimemo python -m pytest tests/ -v
docker compose exec bimemo python -m pytest tests/ -x -q
docker compose exec bimemo python -m pytest --cov=backend --cov-fail-under=100
```

**CRITICAL**: Before EVERY commit, run lint + format + tests **inside the container**:
```bash
docker compose exec bimemo sh -c "ruff check --fix . && ruff format . && python -m pytest --cov=backend --cov-fail-under=100"
```
If ANY errors remain or coverage is below 100% — fix them first. **NEVER** commit code that has lint errors, failing tests, or incomplete test coverage. Zero exceptions.

## What Ruff Handles (don't worry about these)

Ruff enforces: import order, line length (88), quote style (double), trailing commas, unused imports/variables, whitespace, indentation. See `pyproject.toml [tool.ruff]` for full config. **Do not add style rules that duplicate ruff.**

## What Ruff Cannot Enforce (follow these)

### Naming

| Element          | Convention        | Example                   |
|------------------|-------------------|---------------------------|
| Modules          | `snake_case`      | `user_service.py`         |
| Classes          | `PascalCase`      | `UserService`             |
| Exceptions       | `PascalCase+Error`| `ValidationError`         |
| Functions        | `snake_case`      | `get_active_users()`      |
| Constants        | `UPPER_SNAKE`     | `MAX_RETRY_COUNT`         |
| Private          | `_prefix`         | `_build_prompt()`         |

- **NEVER** use single-letter names except `i/j/k` (loops), `e` (exceptions), `f` (files), `x/y/z` (coordinates).
- **NEVER** use transliteration or abbreviations without widely-known meaning.
- Clients: `<Service>Client` → `DatabaseClient`, `HttpClient`.
- Pydantic models: `<Entity>Request`, `<Entity>Response`.

### Constants — MANDATORY

**NEVER** use magic numbers, strings, or hardcoded values in code. **ALWAYS** declare them as named constants.

**Rules:**
- **ALWAYS** place constants at the **top of the file**, after imports and before any function/class definitions.
- **ALWAYS** use `UPPER_SNAKE_CASE` naming.
- **NEVER** scatter hardcoded values inside functions, conditions, or loops.
- If a constant is shared across multiple files — move it to a dedicated `constants.py` module.
- Group related constants together with a short comment explaining the group.

```python
# ✅ Correct — constants at the top, after imports
"""HTTP client for external API communication."""

import httpx

from src.config import get_settings

# --- API defaults ---
DEFAULT_API_VERSION = "v2"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3
MAX_PAGE_SIZE = 100

# --- HTTP status codes ---
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVICE_UNAVAILABLE = 503


class ApiClient:
    def __init__(self, version: str = DEFAULT_API_VERSION) -> None:
        """Initialize API client."""
        self.version = version
        self.timeout = DEFAULT_TIMEOUT_SECONDS

    async def fetch(self, endpoint: str) -> dict:
        """Fetch data from API endpoint."""
        for attempt in range(MAX_RETRIES):
            ...
```

```python
# ✗ NEVER — magic values buried in code
class ApiClient:
    def __init__(self):
        self.version = "v2"              # WRONG — magic string
        self.timeout = 30.0              # WRONG — magic number

    async def fetch(self, endpoint: str):
        for attempt in range(3):          # WRONG — magic number
            ...
        if response.status_code == 429:   # WRONG — magic number
            ...
```

```python
# ✅ Shared constants — src/constants.py
"""Project-wide constants shared across modules."""

# --- Security ---
MAX_INPUT_LENGTH = 10000
ALLOWED_ORIGINS = ("https://example.com",)

# --- Supported languages ---
SUPPORTED_LANGUAGES = ("en", "ru", "lt")

# --- Pagination ---
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
```

### Type Hints

**ALWAYS** use type hints on function signatures and return values (including `-> None`).

```python
# ✅ Modern syntax (Python 3.14+)
def process(items: list[str]) -> dict[str, int]: ...
def fetch(url: str) -> str | None: ...

# ✗ NEVER use legacy typing
from typing import Dict, List, Optional, Union  # WRONG
```

Use `TYPE_CHECKING` for import-only annotations:
```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models import User
```

- If a data shape is stable and crosses module boundaries, prefer a typed
  structure such as `dataclass`, `TypedDict`, or a Pydantic model instead of an
  unstructured `dict[str, Any]`.
- If a state, mode, or status field has a closed set of valid values, prefer
  `Enum` or named constants over scattered raw string literals.
- Boolean-returning helpers and boolean state variables should use explicit
  names that read naturally, such as `is_*`, `has_*`, `can_*`, or `should_*`.

### Docstrings — Google Style

**EVERY function and method MUST have a docstring.** No exceptions — public, private, small or large.

- **Public functions/methods** — full docstring: summary + `Args`, `Returns`, `Raises` where applicable.
- **Private/helper functions** — at minimum a one-line summary describing what the function does.
- **Classes** — full docstring with `Attributes` section.
- **Tests** — at minimum a one-line summary (test name alone is not enough).

```python
# ✅ Public function — full docstring
def calculate_discount(price: float, percent: float) -> float:
    """Calculate discounted price.

    Args:
        price: Original price in base currency.
        percent: Discount percentage (0-100).

    Returns:
        Final price after discount.

    Raises:
        ValueError: If price is negative.
    """

# ✅ Private function — at minimum one-line summary
def _normalize_query(raw: str) -> str:
    """Strip whitespace and collapse multiple spaces into one."""
    return " ".join(raw.split())

# ✅ Short helper — one-liner is enough
def _is_valid_email(value: str) -> bool:
    """Check if value contains an @ symbol and a domain."""
    return "@" in value and "." in value.split("@")[-1]

# ✗ NEVER — function without any docstring
def process_data(items):
    return [x.strip() for x in items]
```

First line: imperative mood, ends with period. Don't repeat type hint info in docstring. **ALWAYS** write docstrings in English.

**Note:** Ruff's `D` rules only enforce docstrings on **public** functions. For **private** functions (`_name`), ruff will not catch missing docstrings — the agent must enforce this rule manually.

### Error Handling

```python
# ✅ Specific exceptions + chain with `from`
try:
    result = await client.get(url)
except httpx.ConnectError as e:
    raise ServiceUnavailableError("API unreachable") from e

# ✗ NEVER do this
try:
    do_something()
except Exception:
    pass
```

- **NEVER** catch bare `Exception` without re-raising.
- **NEVER** silently swallow errors.
- **ALWAYS** use `raise ... from e` to preserve traceback.
- Create project-specific exception hierarchy inheriting from a base error.
- Translate low-level library failures into project-level exceptions at module
  boundaries instead of leaking raw storage, transport, or parsing errors
  through unrelated layers.

### Logging

```python
import logging
logger = logging.getLogger(__name__)

# ✅ Lazy formatting
logger.info("User %s created in %dms", user_id, elapsed_ms)

# ✗ NEVER use f-strings in log calls
logger.info(f"User {user_id} created")  # WRONG — evaluated even if level disabled
```

### Async

- **ALWAYS** use `async/await` for I/O operations (HTTP, DB, file).
- **ALWAYS** use `httpx.AsyncClient` (not `requests`).
- **ALWAYS** set explicit timeouts on HTTP calls.
- **NEVER** call blocking I/O inside async functions.

### Imports

- **NEVER** use `from module import *`.
- **NEVER** use relative imports. Always absolute: `from src.services.auth import AuthService`.
- Ruff handles import sorting — just write them, ruff will reorder.
- Keep imports at the top of the module by default. Use function-local imports
  only for real reasons such as breaking import cycles, optional dependencies,
  or avoiding heavy startup cost.

### Function Design

- Prefer small functions with one clear responsibility.
- Keep the main execution path short, readable, and ordered as a small
  sequence of clear steps.
- If one function performs parsing, validation, scoring, persistence, and
  formatting together, split it into helpers with clear names.
- Orchestration functions may be longer, but they should coordinate smaller
  helpers rather than embed all details inline.
- Prefer returning values from helpers instead of mutating shared state unless
  mutation is the explicit purpose of the function.
- Apply cleanup and simplification rules while writing code, not only during
  later refactoring passes.
- When a code area clearly needs cleanup, prefer a full refactor of that area
  over compatibility shims. Preserve existing user-visible behavior unless the
  task explicitly requires changing it.
- Extract a helper only when it removes real duplication or makes the main path
  easier to understand. Do not split code mechanically just to create more
  functions.
- If a helper, class, or module does not reduce confusion, do not introduce it.

### Code Quality

- Keep one clear responsibility per function, method, and module.
- Remove real duplication of rules, validation, payload shaping, response
  shaping, and state transitions instead of only removing duplicated text.
- Keep side effects explicit. It must be easy to see where data is written,
  where state changes, where external calls happen, and where errors are turned
  into user-facing responses.
- Keep important validation rules in one obvious place. Do not spread the same
  allowlist, size limit, state check, or payload rule across multiple branches.
- When a stable data shape crosses module boundaries, prefer a small typed
  structure over repeated ad-hoc dict construction.
- Use small `dataclass` structures only for coherent grouped data, not as a
  replacement for ordinary local variables.
- Avoid hidden control flow. Important creation, synchronization, retry,
  fallback, deletion, and shutdown behavior should be visible in the main path.
- Prefer boring, obvious code over clever compact code.
- A cleanup is only an improvement if comprehension becomes easier after the
  change.

### Logic vs I/O Boundaries

- Keep pure logic separate from I/O wherever practical.
- Parsing, matching, classification, scoring, and transformation code should be
  kept free of database writes, HTTP calls, filesystem access, or IPC.
- Database access, HTTP calls, filesystem work, subprocesses, and IPC should
  stay in clearly identified boundary or orchestration modules.
- Prefer passing already-loaded data into pure logic helpers instead of letting
  those helpers reach into external systems directly.

### Side Effects

- Functions that mainly compute a result should not also write to storage, emit
  audit events, send network requests, and mutate unrelated state.
- Keep side effects close to orchestration layers so the code path that changes
  the outside world is easy to find and test.
- If a function exists mainly for side effects, make that obvious in its name
  and docstring.

### Configuration

- **ALWAYS** use Pydantic `BaseSettings` for config.
- **NEVER** hardcode secrets, URLs, or credentials.
- **ALWAYS** load from environment variables.
- Provide `.env.example` with all variables (no real values).

### Security Best Practices

Ruff's `S` rules (flake8-bandit) catch some issues automatically (hardcoded passwords, `eval`, weak hashes, etc.). The rules below cover what static analysis **cannot** catch.

#### Secrets & Credentials

- **NEVER** commit `.env`, API keys, passwords, tokens, or private keys.
- **NEVER** hardcode secrets anywhere — not in code, comments, docstrings, or default values.
- **NEVER** log secrets or credentials, even at DEBUG level.
- **ALWAYS** load secrets from environment variables or a secrets manager.
- **ALWAYS** keep `.env` in `.gitignore`. Provide `.env.example` with placeholder values.
- **ALWAYS** use `secrets` module for security tokens, not `random`.

```python
# ✅ Correct
import secrets
token = secrets.token_urlsafe(32)

# ✗ NEVER — random is predictable
import random
token = random.randint(0, 999999)  # INSECURE
```

#### Input Validation & Injection

- **ALWAYS** validate ALL external input with Pydantic models before processing.
- **NEVER** trust data from users, APIs, external services, or message queues.
- **NEVER** construct SQL from strings — use parameterized queries or ORM.
- **NEVER** pass untrusted input to `subprocess`, `os.system`, or `eval`.

```python
# ✅ Parameterized query
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))

# ✗ NEVER — SQL injection
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")  # CRITICAL VULN

# ✅ Safe subprocess — list form, no shell
subprocess.run(["ls", "-la", path], check=True)

# ✗ NEVER — shell injection
subprocess.run(f"ls -la {path}", shell=True)  # CRITICAL VULN
```

#### Deserialization

- **NEVER** deserialize untrusted data with `pickle`, `marshal`, `shelve`, or `yaml.load()`.
- `pickle.loads()` on untrusted data = **remote code execution**.
- **ALWAYS** use `json` for data exchange. If YAML is needed, use `yaml.safe_load()`.

```python
# ✅ Safe
import json
data = json.loads(untrusted_bytes)

import yaml
config = yaml.safe_load(config_file)

# ✗ CRITICAL — arbitrary code execution
import pickle
data = pickle.loads(untrusted_bytes)  # RCE VULNERABILITY

import yaml
data = yaml.load(untrusted_string)   # RCE VULNERABILITY
```

#### Authentication & Authorization

- **NEVER** store passwords in plain text. Use `bcrypt`, `argon2`, or `passlib`.
- **NEVER** implement custom crypto. Use established libraries (`cryptography`, `PyJWT`).
- **ALWAYS** validate JWT tokens: check signature, expiration, issuer, audience.
- **ALWAYS** apply authorization checks at the endpoint level, not just UI.
- **ALWAYS** use constant-time comparison for tokens/hashes (`hmac.compare_digest`).

```python
# ✅ Constant-time comparison prevents timing attacks
import hmac
is_valid = hmac.compare_digest(provided_token, expected_token)

# ✗ NEVER — timing attack
is_valid = provided_token == expected_token  # INSECURE
```

#### HTTP & Network

- **ALWAYS** verify TLS certificates (default in `httpx`/`requests`). **NEVER** set `verify=False` in production.
- **ALWAYS** set timeouts on all HTTP requests. No timeout = potential DoS.
- **ALWAYS** restrict CORS origins to known domains. **NEVER** use `allow_origins=["*"]` in production.
- **ALWAYS** use HTTPS for external APIs.
- **ALWAYS** apply rate limiting to public endpoints.

```python
# ✅ Explicit timeout + TLS verification (httpx defaults)
async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.get("https://api.example.com/data")

# ✗ NEVER in production
response = requests.get(url, verify=False, timeout=None)  # INSECURE
```

#### File Handling

- **NEVER** use user-supplied filenames directly — sanitize or generate UUIDs.
- **ALWAYS** restrict file upload types and sizes.
- **NEVER** use `tempfile.mktemp()` — use `tempfile.mkstemp()` or `tempfile.NamedTemporaryFile()`.
- **ALWAYS** validate file paths to prevent directory traversal (`../../../etc/passwd`).

```python
# ✅ Safe path validation
from pathlib import Path

def safe_path(base_dir: Path, user_filename: str) -> Path:
    """Resolve path and ensure it stays within base_dir."""
    resolved = (base_dir / user_filename).resolve()
    if not resolved.is_relative_to(base_dir.resolve()):
        raise SecurityError("Path traversal detected")
    return resolved
```

#### Dependencies

- **ALWAYS** pin dependency versions in production (`requirements.txt` or `uv.lock`).
- **ALWAYS** audit dependencies regularly: `pip-audit` or `uv audit`.
- **NEVER** install packages from untrusted sources.
- **ALWAYS** use virtual environments — never install into system Python.

#### Version Verification — CRITICAL

**ALWAYS** verify current stable versions of ALL components via web search or official sources **before** writing any code, configs, or `requirements.txt`. Do not rely on training data — it is outdated.

This applies to:
- Python packages (PyPI): `httpx`, `fastapi`, `pydantic`, `sqlalchemy`, `ruff`, etc.
- System components: Python, Docker images (`python:3.14-slim`, `postgres:16`, `redis:7`).
- Base images and tools used in `Dockerfile` and `docker-compose.yml`.
- Any library, SDK, or framework referenced in code or documentation.

**Workflow:**
1. Before starting a project or adding a dependency — search PyPI / GitHub releases for the latest stable version.
2. **NEVER** guess or assume a version number. If unsure — look it up.
3. **ALWAYS** use the latest **stable** release, not alpha/beta/rc unless explicitly requested.
4. When generating `requirements.txt` or `pyproject.toml` — pin to exact verified versions.
5. Periodically re-verify versions for long-running projects.
6. **NEVER** downgrade component versions. If a dependency is already pinned in the project, only update to the **same or newer** version. Resolve compatibility conflicts by upgrading the blocking dependency, not by downgrading others.

```bash
# Quick version check examples
uv pip show fastapi          # Check installed version
pip index versions fastapi   # Check available versions on PyPI
```

```toml
# ✅ Verified and pinned
dependencies = [
    "fastapi==0.115.8",      # Verified 2026-02-13
    "httpx==0.28.1",         # Verified 2026-02-13
    "pydantic==2.10.6",      # Verified 2026-02-13
]

# ✗ NEVER — unverified or unpinned
dependencies = [
    "fastapi",               # No version — unpredictable
    "httpx>=0.24",           # Too loose — may break
    "pydantic==2.5.0",       # Outdated — verify first
]
```

#### Error Handling & Information Disclosure

- **NEVER** expose stack traces, internal paths, or DB errors to end users.
- **ALWAYS** return generic error messages to clients; log details server-side.
- **NEVER** include version numbers, server software, or debug info in production responses.
- **ALWAYS** disable `debug=True` and `/docs` in production.

```python
# ✅ Generic error for client, detailed log for ops
@app.exception_handler(Exception)
async def generic_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})
```

#### Quick Security Checklist

| Check | Rule |
|-------|------|
| Secrets in code?           | `ruff check --select S105,S106,S107` catches some; manual review for edge cases |
| SQL injection?             | Parameterized queries only, never string formatting                             |
| Shell injection?           | `subprocess.run([...], shell=False)`, never f-strings in commands               |
| Unsafe deserialization?    | No `pickle`/`marshal`/`yaml.load()` on untrusted data                           |
| Hardcoded passwords?       | Env variables or secrets manager only                                           |
| TLS verification?          | Never `verify=False` in production                                              |
| Weak crypto?               | `secrets` not `random`; `bcrypt`/`argon2` for passwords                         |
| Path traversal?            | Validate resolved path stays within allowed directory                           |
| Info disclosure?           | Generic errors to clients, details in server logs only                          |
| Dependencies pinned?       | `pip-audit` / `uv audit` in CI                                                 |
| Versions verified?         | **All** component versions checked against PyPI/GitHub before use               |

### Project Structure

```
bimemo/
├── pyproject.toml              # Config, deps, ruff settings
├── CODE_RULES.md               # This file
├── .env.example                # Env template (committed)
├── .env                        # Actual env (NEVER commit)
├── .dockerignore               # Exclude .git, .venv, __pycache__, .env
├── docker/
│   ├── Dockerfile              # Multi-stage build
│   ├── docker-compose.yml      # Production services
│   ├── docker-compose.dev.yml  # Dev overrides (volumes, hot reload)
│   └── entrypoint.sh           # Container entrypoint
├── app/
│   ├── backend/
│   │   ├── config.py           # Pydantic Settings
│   │   ├── app_state.py        # Application state machine
│   │   ├── processes/          # Multi-process entry points (supervisor, db_proxy, etc.)
│   │   ├── ipc/                # Process-level IPC (sockets, protocol, channels)
│   │   ├── bus/                # Worker-level IPC (worker_manager, worker_socket)
│   │   ├── security/           # Encryption, keys, content filtering, audit
│   │   ├── routes/             # FastAPI route handlers
│   │   ├── middleware/         # HTTP middleware stack
│   │   ├── channels/           # Channel adapters (web, sse, telegram, discord)
│   │   ├── gateway/            # Orchestrator, agentic loop, concurrency
│   │   ├── llm/                # LLM providers (Ollama, OpenRouter)
│   │   ├── memory/             # Dialog Recorder, Summary Distiller, context, vector store, FTS
│   │   ├── tools/              # Tool registry, broker, handlers, validation
│   │   ├── trust/              # Trust zones, confirmation
│   │   ├── workers/            # FS/Net worker implementations
│   │   └── bot/                # Telegram and Discord bot adapters
│   └── frontend/
│       └── src/                # React 19 + TypeScript + Vite
└── tests/
    ├── conftest.py             # Shared fixtures
    └── test_*.py
```

- One class/responsibility per file. Split at ~300 lines.
- `__init__.py` — only re-exports, no logic.
- When one area of the codebase grows beyond a few tightly related files, split
  it into a dedicated package directory instead of keeping an ever-growing flat
  folder.
- Group Python modules by responsibility, not by the order they were created.
  Directory boundaries should describe stable responsibilities such as
  classification, routing, parsing, retrieval, validation, or persistence.
- Inside a dedicated package, prefer short role-based filenames such as
  `runtime.py`, `rules.py`, `parsing.py`, `models.py`, `constants.py`, or
  `handlers.py` instead of repeating the parent package name in every filename.
- Use a flat directory only while it stays genuinely small and easy to scan. If
  filenames start growing mainly to compensate for a crowded folder, that is a
  sign the subsystem should become a package.
- Every package directory must have one clear responsibility and a small public
  surface. Keep import convenience in `__init__.py`, but keep all real logic in
  dedicated modules.
- If a package exposes a public entrypoint, make that entrypoint obvious.
  Internal helpers should not become de facto public APIs just because they are
  easy to import from anywhere.
- Temporary compatibility layers, migration shims, aliases, and transitional
  wrappers must be treated as short-lived code. Mark their purpose clearly and
  remove them once the migration is complete.
- Do not add compatibility layers, parallel old/new paths, or temporary wrapper
  logic unless the user explicitly asks for that tradeoff.
- After each finished and verified change, commit it immediately instead of
  accumulating many completed uncommitted edits.

### File Splitting and Code Organization

When a file grows beyond ~300 lines, split it following this pattern:

| File | Contains | Example |
|------|----------|---------|
| `<module>.py` | Main logic — one class or one cohesive group of functions | `process.py` with `OrchestratorProcess` |
| `<module>_models.py` | Dataclasses, Protocols, TypedDicts, Enums | `process_models.py` with `RuntimeServices` |
| `<module>_helpers.py` | Stateless utility functions (pure input → output) | `process_helpers.py` with `parse_optional_int` |
| `<module>_constants.py` | Constants, config values, patterns | `constants.py` with thresholds and prompts |

**Rules:**
- **One file — one responsibility.** Do not mix models, helpers, and business
  logic in the same file. If a file has a class AND standalone helpers AND
  dataclasses — split them.
- **Classes for stateful logic, functions for stateless.** If a group of
  functions passes the same parameters to each other (shared state) — they
  belong in a class. If functions are pure (input → output, no side effects)
  — keep them as module-level functions in `_helpers.py`.
- **Models in a separate file.** Dataclasses, Protocols, and TypedDicts go into
  `_models.py`. This allows importing types without loading heavy logic or
  triggering circular imports.
- **Name files by role, not by technical content.** Use `process.py` not
  `runtime.py`, `env_config_loader.py` not `env_seed.py`,
  `chat_handler.py` not `chat_functions.py`. The filename should tell you
  what the module **does**, not what it **contains**.
- **Helpers must be stateless.** Functions in `_helpers.py` must not reference
  module-level mutable state, class instances, or external services. They
  receive all inputs as parameters and return results.
- **When splitting, remove leading underscores from extracted helpers.**
  Private helpers (`_parse_optional_int`) that move to a dedicated
  `_helpers.py` module become the module's public API — rename to
  `parse_optional_int`.

### Docker & Development Environment

**ALL development happens inside Docker.** No local Python installation, no virtualenv, no bare-metal execution.

**Rules:**
- **ALWAYS** use `docker-compose` to run, test, and debug the application.
- **NEVER** run `python`, `pytest`, `ruff`, or any project command outside a container.
- **NEVER** install project dependencies on the host machine.
- **ALWAYS** provide a `Dockerfile` for every service.
- **ALWAYS** provide a `docker-compose.yml` that starts the full stack with one command.
- **ALWAYS** provide a `docker-compose.dev.yml` with dev overrides (source code volumes, hot reload, debug ports).
- **ALWAYS** include a `.dockerignore` to exclude `.git`, `.venv`, `__pycache__`, `.env`, `node_modules`.

**Dockerfile conventions:**

```dockerfile
# ✅ Multi-stage, non-root, minimal image
FROM python:3.14-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

FROM python:3.14-slim
RUN useradd --create-home appuser
WORKDIR /app
COPY --from=builder /deps /usr/local/lib/python3.14/site-packages
COPY src/ ./src/
COPY tests/ ./tests/
COPY pyproject.toml .
USER appuser
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- **ALWAYS** use multi-stage builds to minimize image size.
- **ALWAYS** run as non-root user (`USER appuser`).
- **ALWAYS** include `ruff` and `pytest` in the image so lint/test commands work inside the container.
- **NEVER** use `latest` tag for base images — pin to specific version (`python:3.14-slim`, not `python:slim`).

**docker-compose.yml conventions:**

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on:
      db: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  db:
    image: postgres:16
    volumes:
      - db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  db-data:
```

**docker-compose.dev.yml — dev overrides:**

```yaml
# docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
services:
  app:
    build:
      target: builder          # Use builder stage with dev deps
    volumes:
      - ./src:/app/src         # Hot reload
      - ./tests:/app/tests     # Live test updates
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      - APP_DEBUG=true
```

- **ALWAYS** add `healthcheck` to every service.
- **ALWAYS** use named volumes for persistent data (DB, models, cache).
- **ALWAYS** use `depends_on` with `condition: service_healthy` — don't start the app before dependencies are ready.
- **NEVER** store data in containers — use volumes.

### Tests

**100% test coverage is MANDATORY.** Every function, branch, and code path must be tested.

- File naming: `test_<module>.py`
- Function naming: `test_<what>_<scenario>()`
- Pattern: Arrange → Act → Assert
- **ALWAYS** add/update tests when changing behavior.
- **ALWAYS** run `pytest --cov=backend --cov-fail-under=100` to verify coverage before committing.
- **NEVER** commit code without corresponding tests.
- **NEVER** reduce coverage — if you add code, you add tests.

**What to test:**
- Every public and private function — happy path + edge cases + error cases.
- All branches (`if/elif/else`, `try/except`, early returns).
- All API endpoints — valid requests, validation errors, auth failures.
- All integrations — mock external services (`httpx`, databases, APIs).

**Coverage configuration in `pyproject.toml`:**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.coverage.run]
source = ["app"]
branch = true

[tool.coverage.report]
fail_under = 100
show_missing = true
exclude_lines = [
    "if TYPE_CHECKING:",
    "pragma: no cover",
]
```

`pragma: no cover` is allowed **only** for genuinely untestable code (e.g., `if __name__ == "__main__":`). Every use must have a comment explaining why.

### Comments

- Explain **why**, not **what**. Code should be self-documenting.
- **ALWAYS** write in English — comments, docstrings, commit messages, TODO notes, error messages in code.
- **NEVER** use Russian, Lithuanian, or any other language in code or documentation.
- TODO format: `# TODO(name): description — ISSUE-123`

### File Header Comment — MANDATORY

**EVERY** `.py` file must start with a module docstring (3–6 sentences) describing:
- What this file/module does and its role in the project.
- What data or entities it works with.
- Key dependencies or services it interacts with.

This is required to help both humans and AI agents quickly understand the project structure without reading the full code.

```python
# ✅ Correct — src/services/api_client.py
"""HTTP client for external API communication.

Provides async methods for sending requests and handling responses.
Works with REST endpoints using httpx.AsyncClient. Handles connection
lifecycle, retries, and timeout management. Used by service layer
for all outbound HTTP communication.
"""

import httpx
...
```

```python
# ✅ Correct — src/security/input_filter.py
"""Input validation and sanitization for incoming requests.

Filters and validates all incoming user data before processing.
Checks for malicious patterns, enforces maximum input length,
and strips potentially dangerous content. Returns a validated
object or raises SecurityError.
"""

from src.models.message import Message
...
```

```python
# ✅ Correct — tests/test_api_client.py
"""Tests for ApiClient request handling and error scenarios.

Covers connection errors, timeouts, retry logic, and response
parsing. Uses httpx mock transport for isolation.
"""

import pytest
...
```

**Rules:**
- Use a module-level docstring (`"""..."""`), not `#` comments.
- **ALWAYS** write in English.
- First line: one-sentence summary of the file's purpose, ends with period.
- Keep it factual and concise — 3 to 6 sentences, no filler.
- **ALWAYS** add this when creating a new file.
- **ALWAYS** update this when the file's purpose changes significantly.

### Git

**CRITICAL — Pre-commit workflow (mandatory, zero exceptions):**

```bash
# Step 1: Autofix + format (inside container)
docker compose exec bimemo ruff check --fix .
docker compose exec bimemo ruff format .

# Step 2: Check for remaining errors
docker compose exec bimemo ruff check .

# Step 3: Run tests with coverage (must be 100%)
docker compose exec bimemo python -m pytest --cov=backend --cov-fail-under=100

# Step 4: ONLY if steps 2+3 have ZERO errors and 100% coverage → commit
git add .
git commit -m "Add user validation"
```

Or as a single command:
```bash
docker compose exec bimemo sh -c "ruff check --fix . && ruff format . && ruff check . && python -m pytest --cov=backend --cov-fail-under=100"
```

- If `ruff check` reports errors that `--fix` could not resolve — **fix them manually before committing**.
- If `pytest` has failing tests — **fix them before committing**.
- If coverage is below 100% — **write missing tests before committing**.
- **NEVER** commit with known lint errors, warnings, test failures, or incomplete coverage. Not even "will fix later".
- **NEVER** use `# noqa`, `# type: ignore`, or `@pytest.mark.skip` to bypass errors without a documented reason.
- Commit messages: imperative mood, concise, **English only** (`Add user validation`, not `Added user validation`).

## Ruff Config Reference

The project uses this `pyproject.toml` config. **Do not modify ruff settings without discussion.**

```toml
[tool.ruff]
target-version = "py314"
line-length = 88
src = ["app", "tests"]
exclude = [".git", ".venv", "__pycache__", "migrations"]

[tool.ruff.lint]
select = [
    "F", "E", "W", "I", "N", "UP", "B", "A", "SIM", "RUF",
    "D", "ANN", "S", "C4", "PT", "RET", "TCH", "LOG", "ASYNC",
]
ignore = ["D100", "D104", "D203", "D213", "S101"]
fixable = ["ALL"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ANN"]
"scripts/**/*.py" = ["D", "ANN"]

[tool.ruff.lint.isort]
known-first-party = ["app"]
lines-after-imports = 2

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
docstring-code-format = true
```
