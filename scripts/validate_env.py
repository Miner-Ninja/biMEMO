"""Pre-deployment gate: check a biMEMO env file before starting the stack.

Usage:
    python3 scripts/validate_env.py [path-to-env]

Reads the env file (default: ``./.env`` in the current working directory),
checks that every required variable is present with a non-empty value, and
reports every missing variable by name in one pass. Variable values are
never printed. Dependency-free: standard library only.

Exit codes:
    0 — all required variables present
    1 — one or more required variables missing or empty
    2 — usage error or the env file was not found
"""

from __future__ import annotations

import sys


# The sixteen required variables of the env-validation spec (R2): the nine
# Core, the one SearXNG, and the six LLM transport/capability variables
# (all required fields of Settings). This table must stay in sync with
# the (REQUIRED) sections of .env.example.
REQUIRED_VARS: tuple[str, ...] = (
    "BIMEMO_DEBUG",
    "BIMEMO_HOST",
    "BIMEMO_PORT",
    "BIMEMO_KEY_SOURCE",
    "BIMEMO_ENCRYPTED_KEY_FILE_PATH",
    "BIMEMO_AUTOMATIC_UNLOCK",
    "BIMEMO_DATABASE_PATH",
    "BIMEMO_UPLOADED_FILES_DIR",
    "BIMEMO_DIAGNOSTICS_RETENTION_DAYS",
    "BIMEMO_SEARXNG_URL",
    "BIMEMO_LLM_RESPONSE_FORMAT",
    "BIMEMO_LLM_RESPONSE_STREAM",
    "BIMEMO_LLM_NATIVE_TOOLS_SUPPORT",
    "BIMEMO_LLM_EXACT_NAMED_CHOICE_SUPPORT",
    "BIMEMO_LLM_REQUIRED_NATIVE_DECISION_SUPPORT",
    "BIMEMO_LLM_CUSTOM_SCHEMA_SUPPORT",
)

# True values follow docker compose semantics, compared case-insensitively.
TRUE_VALUES: frozenset[str] = frozenset({"1", "on", "true", "yes"})
MCP_ENABLED_VAR = "BIMEMO_MCP_ENABLED"
# S105 false positive: the value is the env variable name, not a secret.
MCP_TOKEN_FILE_VAR = "BIMEMO_MCP_TOKEN_FILE"  # noqa: S105

REASON_ABSENT = "absent"
REASON_EMPTY = "empty value"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines into a name-to-value mapping.

    Handles blank lines, ``#`` comment lines, an optional ``export ``
    prefix, and single- or double-quoted values (the quotes are stripped).
    Lines without ``=`` are ignored.

    Args:
        text: Raw content of the env file.

    Returns:
        Mapping of variable name to value with surrounding whitespace and
        enclosing quotes removed.
    """
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()
        env[name] = value
    return env


def _required_set(env: dict[str, str]) -> list[str]:
    """The required set for this env: R2, plus the MCP token file when MCP is on."""
    required = list(REQUIRED_VARS)
    if env.get(MCP_ENABLED_VAR, "").strip().lower() in TRUE_VALUES:
        required.append(MCP_TOKEN_FILE_VAR)
    return required


def validate_env_file(env: dict[str, str]) -> list[tuple[str, str]]:
    """Check the parsed env against the required set and list the gaps.

    A variable counts as missing when it is absent or its value is empty
    after quote and whitespace removal. ``BIMEMO_MCP_TOKEN_FILE`` is
    required only when ``BIMEMO_MCP_ENABLED`` holds a true value.

    Args:
        env: Parsed variables, as returned by :func:`parse_env_file`.

    Returns:
        One ``(name, reason)`` pair per missing variable, in required-set
        order; empty when the env satisfies the gate.
    """
    missing: list[tuple[str, str]] = []
    for name in _required_set(env):
        if name not in env:
            missing.append((name, REASON_ABSENT))
        elif not env[name]:
            missing.append((name, REASON_EMPTY))
    return missing


def main(argv: list[str]) -> int:
    """Run the env gate and return the process exit code.

    Args:
        argv: Process-style argv; the first element is the program name,
            the optional second element the env-file path.

    Returns:
        0 when all required variables are present, 1 when any required
        variable is missing or empty, 2 on a usage error or when the env
        file cannot be read.
    """
    program = argv[0] if argv else "validate_env.py"
    if len(argv) > 2:
        print(f"usage: {program} [path-to-env]", file=sys.stderr)
        return 2
    path = argv[1] if len(argv) == 2 else "./.env"
    try:
        with open(path, encoding="utf-8-sig") as env_file:
            text = env_file.read()
    except FileNotFoundError:
        print(f"env file not found: {path}", file=sys.stderr)
        return 2
    except OSError:
        print(f"cannot read env file: {path}", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print(f"cannot read env file (not valid UTF-8): {path}", file=sys.stderr)
        return 2
    env = parse_env_file(text)
    missing = validate_env_file(env)
    if missing:
        for name, reason in missing:
            print(f"MISSING: {name} ({reason})")
        return 1
    print(f"OK: {len(_required_set(env))} required variables present")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
