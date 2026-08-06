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

import hmac
import logging
import os
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentHeaders

from backend.memory.mcp_seam import (
    MCP_CHANNEL_NAME,
    resolve_conversation_memory,
    resolve_memory_entry,
    resolve_memory_stats,
    resolve_recent_memory,
    resolve_relevant_memory,
)
from backend.security.token_hash import hash_token as _hash_token


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
    "Memory is append-only — no edit or delete of existing entries. "
    "Use search_memory for keyword search, recent_memory for latest entries, "
    "and memory_stats for counts. There is no full-store dump."
)


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


async def _resolve_agent_info(
    headers: dict[str, str],
    cred_client: CredentialClient,
) -> tuple[str, str]:
    """Extract and validate a bearer token; return (capability, agent_id).

    Derives the agent identity from the matching integration's label
    (e.g. ``BIMEMO_MCP_TOKEN_CLAUDE`` -> ``claude``), not the raw hash.

    Args:
        headers: HTTP request headers.
        cred_client: Credential Proxy client (used to load token hashes).

    Returns:
        Tuple of (capability_string, agent_identity_label).

    Raises:
        RuntimeError: If the token is missing, empty, or unknown.
    """
    auth_header = headers.get(_AUTHORIZATION_HEADER, "")
    if not auth_header.lower().startswith(_BEARER_PREFIX):
        raise RuntimeError("Bearer token required")

    token = auth_header[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise RuntimeError("Empty bearer token")

    token_hashes = await _load_agent_token_hashes(cred_client)

    presented_hash = _hash_token(token)
    for stored_hash, capability in token_hashes.items():
        if hmac.compare_digest(presented_hash, stored_hash):
            # Derive agent identity from the label used when the token was seeded.
            # We load integrations again to find the matching label.
            integrations = await cred_client.list_integrations()
            for entry in integrations:
                if entry.get("service_type") != "mcp_agent":
                    continue
                config = entry.get("config") or {}
                if config.get("token_hash") == stored_hash:
                    return capability, (entry.get("name") or stored_hash[:12]).lower()
            return capability, stored_hash[:12].lower()

    raise RuntimeError("Invalid bearer token")


async def _resolve_agent_capability(
    headers: dict[str, str],
    cred_client: CredentialClient,
) -> str:
    """Extract and validate a bearer token by comparing its SHA-256 hash.

    Args:
        headers: HTTP request headers.
        cred_client: Credential Proxy client (used to load token hashes).

    Returns:
        The capability string ("read" or "read-write") for the token.

    Raises:
        RuntimeError: If the token is missing, empty, or unknown.
    """
    capability, _agent_id = await _resolve_agent_info(headers, cred_client)
    return capability


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
            "evidence snippets. Read-only operation. Empty/wildcard query "
            "returns recent entries with a note. Zero matches returns empty "
            "with guidance note."
        ),
    )
    async def search_memory(
        query: str,
        limit: int = 20,
        offset: int = 0,
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Search memory for relevant entries.

        Args:
            query: Natural language search query.
            limit: Maximum results per table (default 20).
            offset: Pagination offset (default 0).
            headers: Injected request headers (excluded from the schema).

        Returns:
            Dict with "results" list and optional "note".
        """
        await _resolve_agent_capability(
            headers, credential_client
        )  # read is sufficient
        return await resolve_relevant_memory(
            admin_client=admin_client,
            query=query,
            limit=limit,
            offset=offset,
        )

    @server.tool(
        name="recent_memory",
        description=(
            "Return the most recent memory entries, newest-first. "
            "Bounded window — not a full store dump. "
            "Use memory_stats for aggregate counts."
        ),
    )
    async def recent_memory(
        limit: int = 20,
        offset: int = 0,
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Return recent memory entries.

        Args:
            limit: Maximum entries (default 20, capped at 50).
            offset: Pagination offset.
            headers: Injected request headers.

        Returns:
            Dict with "results" list and optional "note".
        """
        await _resolve_agent_capability(headers, credential_client)
        return await resolve_recent_memory(
            admin_client=admin_client,
            limit=limit,
            offset=offset,
        )

    @server.tool(
        name="get_memory",
        description=(
            "Fetch a single memory entry by its id. "
            "The id is surfaced by search_memory or recent_memory results. "
            "Returns clear not-found when id is unknown."
        ),
    )
    async def get_memory(
        entry_id: str,
        table: str = "messages",
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Fetch one memory entry by id.

        Args:
            entry_id: The entry's id.
            table: Table to search (messages, records, summaries).
            headers: Injected request headers.

        Returns:
            Dict with "entry" (or None) and optional "note".
        """
        await _resolve_agent_capability(headers, credential_client)
        return await resolve_memory_entry(
            admin_client=admin_client,
            entry_id=entry_id,
            table=table,
        )

    @server.tool(
        name="memory_stats",
        description=(
            "Return aggregate memory statistics: total count, per-type counts, "
            "and date range. Does not return entry content."
        ),
    )
    async def memory_stats(
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Return memory statistics.

        Args:
            headers: Injected request headers.

        Returns:
            Dict with total, by_type counts, and optional date range.
        """
        await _resolve_agent_capability(headers, credential_client)
        return await resolve_memory_stats(admin_client=admin_client)

    @server.tool(
        name="get_memory_by_conversation",
        description=(
            "Return entries for one conversation, newest-first. "
            "Bounded by the read limit. Only returns that conversation's entries."
        ),
    )
    async def get_memory_by_conversation(
        conversation_id: str,
        limit: int = 20,
        offset: int = 0,
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Return entries for one conversation.

        Args:
            conversation_id: The conversation to fetch.
            limit: Maximum entries (default 20, capped at 50).
            offset: Pagination offset.
            headers: Injected request headers.

        Returns:
            Dict with "results" list and optional "note".
        """
        await _resolve_agent_capability(headers, credential_client)
        return await resolve_conversation_memory(
            admin_client=admin_client,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
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
        capability, agent_id = await _resolve_agent_info(
            headers, credential_client
        )
        _require_write_capability(capability)

        full_content = content
        if context:
            full_content = f"{content}\n\nContext: {context}"

        # Route the write through the orchestrator (Socket A); the web-facing
        # FastAPI process must not write memory content directly.
        response = await orchestrator_client.mcp_memory_remember(
            content=full_content,
            channel=MCP_CHANNEL_NAME,
            agent_id=agent_id,
        )
        return {
            "stored": response.stored,
            "message_id": response.message_id,
            "conversation_id": response.conversation_id,
            "reason": response.reason,
        }

    @server.tool(
        name="ask",
        description=(
            "Ask biMEMO a question and receive an answer. The question and "
            "answer are persisted in the agent's session, flowing into the "
            "normal memory pipeline (records, summaries). This is a "
            "two-way synchronous tool — the answer is returned as the tool result."
        ),
    )
    async def ask(
        question: str,
        headers: dict[str, str] = CurrentHeaders(),  # noqa: B008 (FastMCP marker)
    ) -> dict[str, Any]:
        """Ask biMEMO a question and get an answer.

        Args:
            question: The question to ask.
            headers: Injected request headers (excluded from the schema).

        Returns:
            Dict with "answer" text and session identifiers.
        """
        _capability, agent_id = await _resolve_agent_info(
            headers, credential_client
        )

        # R4, R5 — send through orchestrator via typed IPC.
        # The orchestrator resolves the session, runs the LLM loop,
        # and returns the answer. Never uses outbound send.
        response = await orchestrator_client.mcp_ask(
            question=question,
            agent_id=agent_id,
        )
        return {
            "answer": response.answer,
            "conversation_id": response.conversation_id,
            "session_id": response.session_id,
            "error": response.error,
        }

    return server
