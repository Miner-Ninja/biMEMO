#!/usr/bin/env python3
"""OpenAPI spec/code drift gate for the biMEMO service.

Usage:
    python scripts/openapi_check.py           # check the committed spec
    python scripts/openapi_check.py --update  # rewrite the committed spec

Builds the FastAPI application without starting the runtime (the IPC
socket paths and HMAC secrets are only consumed by the lifespan at
startup), generates the OpenAPI document the service advertises at
``/openapi.json``, and compares its canonical serialization (sorted
keys, 2-space indent, one trailing newline) with the committed
``api/openapi.json``. Any drift is a contract violation: the committed
spec must match what the running service advertises.

The check imports the application, so it runs inside the bimemo image
(same container and environment as the test step):

    docker run --rm -v "$PWD:/repo" -w /repo \
      -v "$PWD/.env.example:/app/.env.example:ro" \
      -e PYTHONPATH=/repo/app -e HOME=/tmp \
      docker-bimemo:latest python scripts/openapi_check.py

Exit codes:
    0 — the committed spec matches the generated document, or ``--update``
        rewrote it
    1 — drift: the committed file is missing, not valid JSON, or differs
        from the generated document (a unified diff is printed)
    2 — usage error
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_SPEC = REPO_ROOT / "api" / "openapi.json"

# The IPC socket paths and HMAC secrets are only consumed by the application
# lifespan at startup, which the check never runs; during construction
# (route registration, middleware, OpenAPI generation) they are never
# touched, so the values are neutral placeholders that are never opened
# or created on disk.
_DUMMY_ORCHESTRATOR = ("bimemo-orchestrator-ipc", b"0" * 32)
_DUMMY_DB_PROXY = ("bimemo-db-proxy-ipc", b"1" * 32)


def generate_spec() -> dict:
    """Build the application and return its OpenAPI document.

    The construction path (managers, problem handlers, route
    registration, middleware) is the one the running service uses, so
    the document is identical to the one served at ``/openapi.json``.
    The runtime-only mounts (the ``/mcp`` sub-application, the frontend
    static files) are added later and do not change this document.
    """
    from app.backend.processes.fastapi_app.main import create_app

    application = create_app(
        orchestrator_socket=_DUMMY_ORCHESTRATOR[0],
        orchestrator_secret=_DUMMY_ORCHESTRATOR[1],
        db_proxy_socket_b=_DUMMY_DB_PROXY[0],
        db_proxy_secret_b=_DUMMY_DB_PROXY[1],
    )
    return application.openapi()


def canonical_text(spec: dict) -> str:
    """Deterministic serialization of an OpenAPI document.

    Sorted keys, 2-space indent, one trailing newline: the same document
    always produces the same text, so the comparison is byte-exact.
    """
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def _is_valid_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _display_name(path: Path) -> str:
    """The spec path for reports: relative to the repo root when the
    path is inside it, otherwise the absolute path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check(committed: str | None, generated: str) -> tuple[bool, str]:
    """Compare the committed and generated documents.

    Returns ``(ok, report)``: the report is empty on success; otherwise
    a message, plus a unified diff (committed to generated, so the ``+``
    lines show what the committed file should become) when both sides
    are valid JSON.
    """
    name = _display_name(COMMITTED_SPEC)
    if committed is None:
        return False, (
            f"DRIFT: {name} is missing - run "
            "'python scripts/openapi_check.py --update' to create it"
        )
    if committed == generated:
        return True, ""
    if not _is_valid_json(committed):
        return False, (
            f"DRIFT: {name} is not valid JSON - a valid committed spec "
            "is part of the contract; regenerate it with "
            "'python scripts/openapi_check.py --update'"
        )
    diff = "\n".join(
        difflib.unified_diff(
            committed.splitlines(),
            generated.splitlines(),
            fromfile=f"{name} (committed)",
            tofile=f"{name} (generated from the code)",
            lineterm="",
        )
    )
    return False, (
        f"DRIFT: {name} differs from the OpenAPI document the code "
        f"advertises; regenerate it with 'python scripts/openapi_check.py "
        f"--update' (or revert the code change):\n{diff}"
    )


def update_spec() -> None:
    """Write the canonical serialization of the generated document."""
    COMMITTED_SPEC.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_SPEC.write_text(canonical_text(generate_spec()), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Check that the committed OpenAPI spec matches the code."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite api/openapi.json from the generated document",
    )
    args = parser.parse_args(argv)

    name = _display_name(COMMITTED_SPEC)
    if args.update:
        update_spec()
        print(f"OK: wrote {name} from the generated OpenAPI document")
        return 0

    committed = (
        COMMITTED_SPEC.read_text(encoding="utf-8") if COMMITTED_SPEC.is_file() else None
    )
    ok, report = check(committed, canonical_text(generate_spec()))
    if ok:
        print(f"OK: {name} matches the OpenAPI document the code advertises")
        return 0
    print(report, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
