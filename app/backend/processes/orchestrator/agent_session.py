"""Agent session resolver for the orchestrator process.

Provides a get-or-create helper that maintains exactly one
``chat_bridge_links`` row per MCP agent identity. All memory writes
for a given agent are routed into that single conversation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .client import DBProxyClient


def _mcp_session_id(agent_id: str) -> str:
    """Return a stable session identifier for one agent.

    The session ID is the same as the agent ID — there is one session
    per agent and it lives as long as the agent exists.
    """
    return agent_id


async def resolve_agent_session(
    *,
    agent_id: str,
    db_client: DBProxyClient,
) -> tuple[str, str]:
    """Get or create a single persistent session for one MCP agent.

    Looks up an existing ``chat_bridge_links`` row with ``provider="mcp"``
    and ``external_space_id=<agent_id>``. When absent, creates a new
    conversation and bridge link so the agent has a home for all its
    memory. When present, returns the existing identifiers.

    This is the get-or-create that enforces ONE session per agent — the
    ``external_channel_id`` is a fixed constant ``"mcp"`` so the UNIQUE
    constraint on ``(provider, external_space_id, external_channel_id)``
    guarantees at most one row per agent.

    Args:
        agent_id: Stable identifier for the requesting agent (derived from
            the bearer token label or hash).
        db_client: DB Proxy client for IPC operations.

    Returns:
        Tuple of ``(conversation_id, session_id)`` for the agent's
        persistent session.
    """
    # Try to find an existing bridge link for this agent.
    existing = await db_client.send_operation(
        "get_chat_bridge_by_external_target",
        {
            "provider": "mcp",
            "external_space_id": agent_id,
            "external_channel_id": "mcp",
        },
    )
    link = existing.get("chat_bridge_link")
    if link is not None:
        return link["conversation_id"], _mcp_session_id(agent_id)

    # No session yet — create one.
    conversation_id = await db_client.create_conversation(
        title=f"Agent: {agent_id}",
    )
    await db_client.send_operation(
        "create_chat_bridge_link",
        {
            "conversation_id": conversation_id,
            "provider": "mcp",
            "external_space_id": agent_id,
            "external_channel_id": "mcp",
            "created_by": agent_id,
            "sync_direction": "external_to_web",
            "status": "active",
            "delivery_mode": "bot_only",
            "persona_mode": "neutral",
            "persona_user_id": "",
        },
    )
    return conversation_id, _mcp_session_id(agent_id)
