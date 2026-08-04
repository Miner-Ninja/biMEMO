"""MCP (Model Context Protocol) endpoint for external agent access.

Serves a FastMCP server over Streamable HTTP transport mounted into the
existing biMEMO FastAPI app. Authenticates clients with static bearer
tokens loaded from a file and exposes two tools: ``search_memory``
(read-only) and ``remember`` (append-only).

The token map is passed into ``build_mcp_server`` and captured by the
tool closures; there is no mutable module-level token state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentHeaders

from backend.memory.mcp_seam import MCP_CHANNEL_NAME, resolve_relevant_memory


if TYPE_CHECKING:
    from pathlib import Path

    from backend.processes.fastapi_app.admin_db_client import AdminDBClient
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


def _load_mcp_tokens(token_file: Path) -> dict[str, str]:
    """Load bearer tokens and their capabilities from a file.

    Each non-empty, non-comment line must be ``<token> <capability>`` where
    capability is ``read`` or ``read-write``.

    Args:
        token_file: Path to the token file.

    Returns:
        Dict mapping token strings to capability strings.

    Raises:
        ValueError: If a line has an invalid format or unknown capability.
    """
    tokens: dict[str, str] = {}

    raw = token_file.read_text().splitlines()
    for line_num, line in enumerate(raw, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            msg = f"Token file line {line_num}: expected '<token> <capability>'"
            raise ValueError(msg)
        token, capability = parts
        if capability not in _VALID_CAPABILITIES:
            valid = ", ".join(sorted(_VALID_CAPABILITIES))
            msg = (
                f"Token file line {line_num}: capability '{capability}' "
                f"is invalid. Must be one of: {valid}"
            )
            raise ValueError(msg)
        tokens[token] = capability

    logger.info("Loaded %d MCP token(s)", len(tokens))
    return tokens


def _validate_bearer_capability(
    headers: dict[str, str],
    tokens: dict[str, str],
) -> str:
    """Extract and validate the bearer token from HTTP headers.

    Args:
        headers: HTTP request headers.
        tokens: Map of valid token strings to capability strings.

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

    capability = tokens.get(token)
    if capability is None:
        raise RuntimeError("Invalid bearer token")

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


def build_mcp_server(
    admin_client: AdminDBClient,
    orchestrator_client: OrchestratorClient,
    tokens: dict[str, str],
) -> FastMCP:
    """Build the FastMCP server with the configured tools.

    Args:
        admin_client: DB Proxy client (Socket B) for memory reads.
        orchestrator_client: Orchestrator IPC client; the write path for
            ``remember`` so memory content is never written directly by the
            web-facing FastAPI process.
        tokens: Map of valid bearer tokens to their capability strings.

    Returns:
        Configured FastMCP server with search_memory and remember tools.

    Note:
        ``CurrentHeaders()`` is placed directly as a parameter default (not
        wrapped in ``Depends``); FastMCP resolves it to the current request's
        headers and excludes the parameter from the tool's input schema.
    """
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
        _validate_bearer_capability(headers, tokens)  # read is sufficient
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
        capability = _validate_bearer_capability(headers, tokens)
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
