"""Skill DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for skill CRUD, FTS5 search, and usage logging. Each function receives a
DBProxyClient instance as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def create_skill(
    client: DBProxyClient,
    name: str,
    title: str,
    description: str,
    trigger: str = "",
    procedure: str = "[]",
    errors: str = "[]",
    verification: str = "",
    tool_sequence: str = "",
) -> dict[str, Any]:
    """Create or update a skill.

    Args:
        client: DB Proxy client instance.
        name: Kebab-case unique identifier.
        title: Human-readable name.
        description: One-line summary.
        trigger: When to use this skill.
        procedure: JSON array of procedure steps.
        errors: JSON array of error/fix pairs.
        verification: How to verify the result.
        tool_sequence: Comma-separated tool names.

    Returns:
        Confirmation dict with skill name and status.
    """
    return await client.send_operation(
        "skill_create",
        {
            "name": name,
            "title": title,
            "description": description,
            "trigger": trigger,
            "procedure": procedure,
            "errors": errors,
            "verification": verification,
            "tool_sequence": tool_sequence,
        },
    )


async def search_skills(
    client: DBProxyClient,
    query: str,
) -> list[dict[str, Any]]:
    """Search skills via FTS5.

    Args:
        client: DB Proxy client instance.
        query: Search query string.

    Returns:
        List of matching skill dicts with full content.
    """
    return await client._send_list_operation(
        "skill_search",
        result_key="skills",
        parameters={"query": query},
    )


async def increment_skill_use(
    client: DBProxyClient,
    skill_id: str,
) -> dict[str, Any]:
    """Increment use_count for a skill.

    Args:
        client: DB Proxy client instance.
        skill_id: Skill UUID.

    Returns:
        Status dict.
    """
    return await client.send_operation(
        "skill_use",
        {"skill_id": skill_id},
    )


async def list_skills(client: DBProxyClient) -> list[dict[str, Any]]:
    """List all skills as a compact index.

    Args:
        client: DB Proxy client instance.

    Returns:
        List of skill dicts with name, title, description, use_count.
    """
    return await client._send_list_operation(
        "skill_list",
        result_key="skills",
        parameters={},
    )


async def log_skill_usage(
    client: DBProxyClient,
    skill_id: str,
    question: str,
    status: str = "success",
    duration_ms: int = 0,
    note: str = "",
) -> dict[str, Any]:
    """Log a skill usage entry.

    Args:
        client: DB Proxy client instance.
        skill_id: Skill UUID.
        question: User question text.
        status: Result status (success/partial/error).
        duration_ms: Response duration.
        note: Optional note.

    Returns:
        Log entry ID.
    """
    return await client.send_operation(
        "skill_usage_log_create",
        {
            "skill_id": skill_id,
            "question": question,
            "status": status,
            "duration_ms": duration_ms,
            "note": note,
        },
    )


async def delete_skill(
    client: DBProxyClient,
    name: str,
) -> dict[str, Any]:
    """Delete a skill by name.

    Args:
        client: DB Proxy client instance.
        name: Skill name to delete.

    Returns:
        Status dict.
    """
    return await client.send_operation(
        "skill_delete",
        {"name": name},
    )
