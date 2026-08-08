"""Message persistence delegate for the gateway orchestrator.

Saves user and assistant messages, builds message metadata,
and persists compact dialog-step records used by memory
compression. Supports both sync and async save callables.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    build_assistant_message_meta,
    build_dialog_step_record_input,
)
from backend.processes.orchestrator.client import DBProxyError


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.llm.structured_response import StructuredAssistantContent
    from backend.models.chat_stats import ExtendedUsage


logger = logging.getLogger(__name__)

# --- Recoverable errors for persistence callbacks ---
RECOVERABLE_MEMORY_CALLBACK_ERRORS = (
    DBProxyError,
    RuntimeError,
    TypeError,
    ValueError,
)


class MessagePersistence:
    """Save user/assistant messages and dialog records.

    Attributes:
        save_fn: Callable to persist individual messages.
        save_record_fn: Callable to persist dialog-step records.
    """

    def __init__(
        self,
        save_fn: Callable | None,
        save_record_fn: Callable | None,
    ) -> None:
        """Initialize message persistence.

        Args:
            save_fn: Callable to persist individual messages.
            save_record_fn: Callable to persist dialog-step records.
        """
        self.save_fn = save_fn
        self.save_record_fn = save_record_fn

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str | list[dict[str, object]],
        *,
        meta: dict[str, object] | None = None,
    ) -> str | None:
        """Persist one conversation message when storage is available."""
        if self.save_fn is None:
            return None
        if inspect.iscoroutinefunction(self.save_fn):
            return await self.save_fn(
                conversation_id,
                role,
                content,
                meta=meta,
            )
        return self.save_fn(
            conversation_id,
            role,
            content,
            meta=meta,
        )

    async def save_user_message(
        self,
        *,
        conversation_id: str,
        user_content: str | list[dict[str, object]],
        attached_files: list[str],
        uploaded_images: list[str],
        meta: dict | None,
    ) -> str | None:
        """Persist the incoming user message with optional metadata."""
        message_meta = self.build_user_message_meta(
            attached_files=attached_files,
            uploaded_images=uploaded_images,
            meta=meta,
        )
        return await self.save_message(
            conversation_id,
            "user",
            user_content,
            meta=message_meta,
        )

    def build_user_message_meta(
        self,
        *,
        attached_files: list[str],
        uploaded_images: list[str],
        meta: dict | None,
    ) -> dict[str, object] | None:
        """Build persisted metadata for one incoming user message."""
        if not attached_files and not uploaded_images and not meta:
            return None

        message_meta: dict[str, object] = {}
        if attached_files:
            message_meta["attached_files"] = attached_files
        if uploaded_images:
            message_meta["images_count"] = len(uploaded_images)
        if meta:
            message_meta.update(meta)
        return message_meta

    async def save_dialog_record(
        self,
        *,
        conversation_id: str,
        session_id: str,
        filtered_message: str,
        response_text: str,
        user_message_id: str | None,
        assistant_message_id: str | None,
    ) -> None:
        """Persist the compact dialog-step record used by memory compression."""
        if self.save_record_fn is None or not response_text:
            return
        try:
            record_input = build_dialog_step_record_input(
                filtered_message,
                response_text,
            )
            await self.persist_dialog_record(
                conversation_id=conversation_id,
                session_id=session_id,
                record_input=record_input,
                source_message_ids=self.build_dialog_record_source_ids(
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_message_id,
                ),
            )
        except RECOVERABLE_MEMORY_CALLBACK_ERRORS:
            logger.warning(
                "Failed to save record",
                exc_info=True,
            )

    def build_dialog_record_source_ids(
        self,
        *,
        user_message_id: str | None,
        assistant_message_id: str | None,
    ) -> list[str]:
        """Build the source message id list for one dialog-step record."""
        source_message_ids: list[str] = []
        if user_message_id:
            source_message_ids.append(user_message_id)
        if assistant_message_id:
            source_message_ids.append(assistant_message_id)
        return source_message_ids

    async def persist_dialog_record(
        self,
        *,
        conversation_id: str,
        session_id: str,
        record_input: str,
        source_message_ids: list[str],
    ) -> None:
        """Write one dialog-step record through the configured save callback."""
        # Skip dialog recorder for scheduler sessions — prevents memory
        # pollution from repetitive scheduled tasks.
        if session_id.startswith("sched_") or conversation_id.startswith("sched_"):
            return
        if inspect.iscoroutinefunction(self.save_record_fn):
            await self.save_record_fn(
                conversation_id,
                session_id,
                record_input,
                source_message_ids,
            )
            return
        self.save_record_fn(
            conversation_id,
            session_id,
            record_input,
            source_message_ids,
        )

    async def save_final_assistant_message(
        self,
        *,
        conversation_id: str,
        raw_assistant_text: str,
        stats: ExtendedUsage | None,
        structured_content: StructuredAssistantContent | None,
    ) -> str | None:
        """Persist the raw final assistant text with usage metadata."""
        return await self.save_message(
            conversation_id,
            "assistant",
            raw_assistant_text,
            meta=build_assistant_message_meta(stats, structured_content),
        )

    async def persist_final_turn_messages(
        self,
        *,
        conversation_id: str,
        session_id: str,
        filtered_message: str,
        raw_assistant_text: str,
        response_text: str,
        stats: ExtendedUsage | None,
        structured_content: StructuredAssistantContent | None,
        user_message_id: str | None,
    ) -> str | None:
        """Persist the assistant message and the compact dialog-step record."""
        assistant_message_id = await self.save_final_assistant_message(
            conversation_id=conversation_id,
            raw_assistant_text=raw_assistant_text,
            stats=stats,
            structured_content=structured_content,
        )
        await self.save_dialog_record(
            conversation_id=conversation_id,
            session_id=session_id,
            filtered_message=filtered_message,
            response_text=response_text,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )
        return assistant_message_id
