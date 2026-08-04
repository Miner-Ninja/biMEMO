"""MCP (Model Context Protocol) endpoint for external agent access.

Serves a FastMCP server over Streamable HTTP transport mounted into the
existing biMEMO FastAPI app. Authenticates clients with bearer tokens
stored as SHA-256 hashes in the integrations table (service_type="mcp_agent")
via the Credential Proxy (Socket C). Exposes two tools: ``search_memory``
(read-only) and ``remember`` (append-only).

Token hashes are loaded at startup from the database; there is no mutable
module-level token state.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentHeaders

from backend.memory.mcp_seam import MCP_CHANNEL_NAME, resolve_relevant_memory


if TYPE_CHECKING:
    from backend.processes.fastapi_app.admin_db_client import AdminDBClient
    from backend.processes.fastapi_app.credential_client import CredentialClient
    from backend.processes.fastapi_app.orchestrator_client import (
        OrchestratorClient,
    )


logger = logging.getLogger(__name__)

MCP_READ_CAPABILITY = "read"
MCP_READ_WRITE_CAPABILITY = "read-write"
_VALID_CAPABILITIES = frozenset({MCP_READ_CAPABILITY, MCP_READ_WRITE_CAPABILITY})
_AUTHORIZATION_HEADER = "authorization"
_BEARER_PREFIX = "bearer "

_SERVER_INSTRUCTIONS = (
    "biMEMO memory access for external coding agents. "
    "Use search_memory to find relevant records, summaries, or messages. "
    "Use remember to store new information. All writes are append-only; "
    "there is no delete or update."
)


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a plaintext token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _load_agent_token_hashes(
    cred_client: CredentialClient,
) -> dict[str, str]:
    """Load agent token hashes and capabilities from the database.

    Queries all integrations with ``service_type == 'mcp_agent'`` and builds
    a map of ``token_hash -> capability`` from each integration's ``config``
    field.

    Args:
        cred_client: Credential Proxy client for database access.

    Returns:
        Dict mapping SHA-256 hex strings to capability strings.
    """
    integrations = await cred_client.list_integrations()
    tokens: dict[str, str] = {}
    for entry in integrations:
        if entry.get("service_type") != "mcp_agent":
            continue
        config = entry.get("config") or {}
        token_hash = config.get("token_hash")
        capability = config.get("capability")
        if token_hash and capability:
            tokens[token_hash] = capability

    logger.info("Loaded %d MCP agent token hash(es)", len(tokens))
    return tokens


async def _resolve_agent_capability(
    headers: dict[str, str],
    cred_client: CredentialClient,
    token_hashes: dict[str, str] | None = None,
) -> str:
    """Extract and validate a bearer token by comparing its SHA-256 hash.

    Args:
        headers: HTTP request headers.
        cred_client: Credential Proxy client (used to load token hashes).
        token_hashes: Pre-loaded token hash map; loaded from DB if not given.

    Returns:
        The capability string ("read" or "read-write") for the token.

    Raises:
        RuntimeError: If the token is missing, empty, or unknown.
    """
    auth_header = headers.get(_AUTHORIZATION_HEADER, "")
    if not auth_header.lower().startswith(_BEARER_PREFIX):
        raise RuntimeError("Bearer token required")

    token = auth_header[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise RuntimeError("Empty bearer token")

    if token_hashes is None:
        token_hashes = await _load_agent_token_hashes(cred_client)

    presented_hash = _hash_token(token)
    for stored_hash, capability in token_hashes.items():
        if hmac.compare_digest(presented_hash, stored_hash):
            return capability

    raise RuntimeError("Invalid bearer token")


def _require_write_capability(capability: str) -> None:
    """Raise if the token does not have write permission.

    Args:
        capability: Token capability string.

    Raises:
        RuntimeError: If the token is read-only.
    """
    if capability != MCP_READ_WRITE_CAPABILITY:
        raise RuntimeError("Read-only token cannot write")


async def seed_mcp_tokens_from_env(
    credential_client: CredentialClient,
) -> None:
    """Seed or refresh MCP agent tokens from ``BIMEMO_MCP_TOKEN_<label>`` env vars.

    Each matching env var must be ``<token>:<capability>`` where capability is
    ``read`` or ``read-write``. The label is derived from the env var name
    (e.g. ``BIMEMO_MCP_TOKEN_CLAUDE`` -> label ``claude``).

    For each env label the function upserts an ``mcp_agent`` integration:
    creates it if absent, updates ``config`` if it changed. It only ever
    touches the labels present in the environment.

    Tokens the environment does not name are left untouched — this includes
    UI-issued agent tokens. A label already used by a non-agent integration
    is skipped rather than overwritten.

    Args:
        credential_client: Credential Proxy client for integration CRUD.

    Raises:
        ValueError: If a value is malformed (missing colon, bad capability).
    """
    env_prefix = "BIMEMO_MCP_TOKEN_"
    env_tokens: dict[str, tuple[str, str]] = {}
    for env_name, env_value in os.environ.items():
        if not env_name.startswith(env_prefix):
            continue
        label = env_name[len(env_prefix) :].lower()
        if ":" not in env_value:
            raise ValueError(
                f"{env_name}: expected '<token>:<capability>' format"
            )
        token, capability = env_value.split(":", 1)
        if capability not in _VALID_CAPABILITIES:
            valid = ", ".join(sorted(_VALID_CAPABILITIES))
            raise ValueError(
                f"{env_name}: capability '{capability}' is invalid. "
                f"Must be one of: {valid}"
            )
        env_tokens[label] = (token, capability)

    # Index existing integrations. Agent tokens are keyed by label so we can
    # upsert only our own; non-agent names are guarded so we never clobber a
    # UI/LLM integration that happens to share a name.
    existing = await credential_client.list_integrations()
    existing_agents = {
        (e.get("name") or "").lower(): e
        for e in existing
        if e.get("service_type") == "mcp_agent"
    }
    non_agent_names = {
        (e.get("name") or "").lower()
        for e in existing
        if e.get("service_type") != "mcp_agent"
    }

    # Upsert only the labels named in the environment. Labels absent from the
    # environment — including UI-issued tokens — are left untouched.
    for label, (token, capability) in env_tokens.items():
        if label in non_agent_names:
            logger.warning(
                "Skipping agent token %s: name %r already used by a "
                "non-agent integration",
                env_prefix + label.upper(),
                label,
            )
            continue

        token_hash = _hash_token(token)
        config = {"token_hash": token_hash, "capability": capability}
        existing_entry = existing_agents.get(label)
        if existing_entry is not None:
            current = existing_entry.get("config") or {}
            if (
                current.get("token_hash") == token_hash
                and current.get("capability") == capability
            ):
                continue  # already up to date — nothing to do
            # Config changed: replace this agent token in place.
            await credential_client.delete_integration(existing_entry["name"])
        await credential_client.create_integration(
            name=label,
            integration_type="mcp_agent",
            config=config,
        )
        logger.info("Seeded MCP agent token: %s (%s)", label, capability)


def build_mcp_server(
    admin_client: AdminDBClient,
    orchestrator_client: OrchestratorClient,
    credential_client: CredentialClient,
) -> FastMCP:
    """Build the FastMCP server with the configured tools.

    Args:
        admin_client: DB Proxy client (Socket B) for memory reads.
        orchestrator_client: Orchestrator IPC client; the write path for
            ``remember`` so memory content is never written directly by the
            web-facing FastAPI process.
        credential_client: Credential Proxy client for agent token validation.

    Returns:
        Configured FastMCP server with search_memory and remember tools.

    Note:
        ``CurrentHeaders()`` is placed directly as a parameter default (not
        wrapped in ``Depends``); FastMCP resolves it to the current request's
        headers and excludes the parameter from the tool's input schema.
    """
    # Token hashes are loaded per request inside the tools (so revocation and
    # newly-issued tokens take effect immediately), not cached at build time.
    server = FastMCP(name="biMEMO Memory", instructions=_SERVER_INSTRUCTIONS)

    @server.tool(
        name="search_memory",
        description=(
            "Search biMEMO's memory for relevant records, summaries, or "
            "messages matching the query. Returns structured results with "
            "evidence snippets. Read-only operation."
        ),
    )
    async def search_memory(
        query: str,
        limit: int = 20,
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> list[dict[str, Any]]:
        """Search memory for relevant entries.

        Args:
            query: Natural language search query.
            limit: Maximum results per table (default 20).
            headers: Injected request headers (excluded from the schema).

        Returns:
            List of matching memory entries with table type and snippet.
        """
        await _resolve_agent_capability(
            headers, credential_client
        )  # read is sufficient
        return await resolve_relevant_memory(
            admin_client=admin_client,
            query=query,
            limit=limit,
        )

    @server.tool(
        name="remember",
        description=(
            "Store a new memory entry in biMEMO. The content is processed by "
            "biMEMO's dialog recorder and may generate records and summaries. "
            "Append-only: cannot update or delete existing memories."
        ),
    )
    async def remember(
        content: str,
        context: str = "",
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Remember a piece of information.

        Args:
            content: The content to store in memory.
            context: Optional additional context for the entry.
            headers: Injected request headers (excluded from the schema).

        Returns:
            Dict with "stored", "message_id", and "conversation_id".
        """
        capability = await _resolve_agent_capability(
            headers, credential_client
        )
        _require_write_capability(capability)

        full_content = content
        if context:
            full_content = f"{content}\n\nContext: {context}"

        # Route the write through the orchestrator (Socket A); the web-facing
        # FastAPI process must not write memory content directly.
        response = await orchestrator_client.mcp_remember(
            content=full_content,
            channel=MCP_CHANNEL_NAME,
        )
        return {
            "stored": response.stored,
            "message_id": response.message_id,
            "conversation_id": response.conversation_id,
        }

    return server
