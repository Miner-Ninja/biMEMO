"""Skill tool handlers for create, search, and load operations.

Creates, searches, and loads skills through the DB proxy path.
Skill_create saves or updates a reusable recipe.
Skill_search finds relevant skills via FTS5 and increments use count.
Skill_load retrieves full content of a skill by name.
"""

from backend.tools.builtin import (
    SkillCreateInput,
    SkillLoadInput,
    SkillSearchInput,
)


def make_skill_create_handler(
    db_client: object,
) -> "callable":
    """Create a skill_create handler backed by DB Proxy IPC.

    Args:
        db_client: DBProxyClient with send_operation method.

    Returns:
        Async handler function.
    """

    async def handle_skill_create(
        params: SkillCreateInput,
    ) -> str:
        """Save or update a skill via DB Proxy IPC.

        Args:
            params: Validated skill creation input.

        Returns:
            Confirmation string.
        """
        result = await db_client.send_operation(
            "skill_create",
            {
                "name": params.name,
                "title": params.title,
                "description": params.description,
                "trigger": params.trigger,
                "procedure": params.procedure,
                "errors": params.errors,
                "verification": params.verification,
                "tool_sequence": params.tool_sequence,
            },
        )
        name = result.get("name", params.name)
        return f"Skill '{name}' saved."

    return handle_skill_create


def make_skill_search_handler(
    db_client: object,
) -> "callable":
    """Create a skill_search handler backed by DB Proxy IPC.

    Args:
        db_client: DBProxyClient with send_operation method.

    Returns:
        Async handler function.
    """

    async def handle_skill_search(
        params: SkillSearchInput,
    ) -> str:
        """Search skills via DB Proxy IPC and increment use counts.

        Args:
            params: Validated skill search input.

        Returns:
            Formatted string of matching skills or no-results message.
        """
        result = await db_client.send_operation(
            "skill_search",
            {"query": params.query},
        )
        skills = result.get("skills", [])
        if not skills:
            return "No matching skills found."

        # Increment use_count for returned skills
        for skill in skills:
            skill_id = skill.get("id")
            if skill_id:
                await db_client.send_operation(
                    "skill_use",
                    {"skill_id": skill_id},
                )

        parts = []
        for skill in skills:
            name = skill.get("name", "")
            title = skill.get("title", "")
            desc = skill.get("description", "")
            parts.append(f"- {name}: {title} — {desc}")

        return "\n\n---\n\n".join(parts)

    return handle_skill_search


def make_skill_load_handler(
    db_client: object,
) -> "callable":
    """Create a skill_load handler backed by DB Proxy IPC.

    Args:
        db_client: DBProxyClient with send_operation method.

    Returns:
        Async handler function.
    """

    async def handle_skill_load(
        params: SkillLoadInput,
    ) -> str:
        """Load full skill content by name via DB Proxy IPC.

        Args:
            params: Validated skill load input.

        Returns:
            Full skill content or not-found message.
        """
        result = await db_client.send_operation(
            "skill_load",
            {"name": params.name},
        )
        skill = result.get("skill")
        if not skill:
            return f"Skill '{params.name}' not found."
        # Increment use count
        skill_id = skill.get("id")
        if skill_id:
            await db_client.send_operation(
                "skill_use",
                {"skill_id": skill_id},
            )
        assembled = skill.get("assembled_text", "")
        title = skill.get("title", "")
        return (
            f"# {title}\n\n{assembled}"
            if assembled
            else f"Skill '{params.name}' has no content."
        )

    return handle_skill_load
