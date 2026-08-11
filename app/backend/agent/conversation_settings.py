"""Read per-conversation display settings needed at turn time.

The web tier stores per-conversation toggles (e.g. show_suggestions) in the
chat_settings key-value table. Most are display-only and never reach the agent,
but show_suggestions must gate generation, so the orchestrator and the
scheduler read it here before running a turn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)

SHOW_SUGGESTIONS_KEY = "show_suggestions"
THINKING_ENABLED_KEY = "thinking_enabled"


async def read_suggestions_enabled(
    db_client: DBProxyClient,
    conversation_id: str,
    *,
    default: bool,
) -> bool:
    """Return a conversation's show_suggestions setting, or ``default`` if unset.

    Never raises: on a missing conversation id or any read error the default is
    returned, so a settings hiccup cannot break a chat turn.
    """
    if not conversation_id:
        return default
    try:
        result = await db_client.send_operation(
            "list_chat_settings", {"conversation_id": conversation_id}
        )
    except Exception:
        logger.warning(
            "Failed to read chat settings for %s; using default suggestions=%s",
            conversation_id,
            default,
            exc_info=True,
        )
        return default
    settings = result.get("settings", {}) if isinstance(result, dict) else {}
    raw = settings.get(SHOW_SUGGESTIONS_KEY)
    if raw is None:
        return default
    return raw == "1"


async def read_thinking_enabled(
    db_client: DBProxyClient,
    conversation_id: str,
    *,
    default: bool = True,
) -> bool:
    """Return a conversation's thinking_enabled setting, or ``default``.

    Never raises: on a missing conversation id or any read error the
    default is returned, so a settings hiccup cannot break a chat turn.
    """
    if not conversation_id:
        return default
    try:
        result = await db_client.send_operation(
            "list_chat_settings", {"conversation_id": conversation_id}
        )
    except Exception:
        logger.warning(
            "Failed to read chat settings for %s; using default thinking=%s",
            conversation_id,
            default,
            exc_info=True,
        )
        return default
    settings = result.get("settings", {}) if isinstance(result, dict) else {}
    raw = settings.get(THINKING_ENABLED_KEY)
    if raw is None:
        return default
    return raw == "1"
