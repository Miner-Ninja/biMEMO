"""Stateless utility functions for the orchestrator process.

Provides helpers for config parsing, timezone resolution, media
client initialization, and queue record building. All functions
are pure or factory-style with no shared state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.config import Settings

    from .process_models import DialogRecorderQueueProtocol


logger = logging.getLogger(__name__)


def single_configured_id(configured_ids: frozenset[str]) -> str:
    """Return the only configured id when the set is unambiguous."""
    if len(configured_ids) != 1:
        return ""
    return next(iter(configured_ids))


def parse_optional_int(raw_value: object) -> int | None:
    """Parse an optional integer from config values stored as strings."""
    if raw_value in (None, ""):
        return None
    try:
        return int(str(raw_value))
    except TypeError, ValueError:
        logger.warning(
            "Ignoring invalid integer config value: %r",
            raw_value,
        )
        return None


def resolve_user_tz(profile: dict[str, str]) -> ZoneInfo | None:
    """Extract ZoneInfo from user profile, or None if invalid/missing."""
    tz_name = profile.get("timezone", "")
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except KeyError, ValueError:
        logger.warning("Invalid timezone in user profile: %r", tz_name)
        return None


def current_processing_timestamp() -> str:
    """Return the current UTC timestamp in the database text format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


def init_stt_client(settings: Settings) -> object | None:
    """Initialize the speech-to-text client when configured."""
    if not settings.whisper_endpoint:
        return None
    from backend.llm.stt_client import STTClient

    stt_client = STTClient(
        endpoint=settings.whisper_endpoint,
        language=settings.whisper_language,
        model=settings.whisper_model,
    )
    logger.info(
        "STT client initialized (endpoint=%s, language=%s, model=%s)",
        settings.whisper_endpoint,
        settings.whisper_language,
        settings.whisper_model,
    )
    return stt_client


def init_tts_client(settings: Settings) -> object | None:
    """Initialize the text-to-speech client when configured."""
    if not settings.tts_endpoint:
        return None
    from backend.llm.tts_client import TTSClient

    tts_client = TTSClient(
        endpoint=settings.tts_endpoint,
        voice_id=settings.tts_voice_id,
    )
    logger.info(
        "TTS client initialized (endpoint=%s)",
        settings.tts_endpoint,
    )
    return tts_client


def build_queue_record_fn(
    queue: DialogRecorderQueueProtocol,
) -> Callable[[str, str, str, list[str] | None], Awaitable[None]]:
    """Build the queue-backed record writer for the dialog recorder pipeline."""
    from backend.memory.runtime.rate_limiter_models import DialogRecorderQueueItem

    async def queue_record_fn(
        conversation_id: str,
        session_id: str,
        summary: str,
        source_message_ids: list[str] | None = None,
    ) -> None:
        """Queue one dialog recorder record for background processing."""
        queue.put(
            DialogRecorderQueueItem(
                messages=[summary],
                conversation_id=conversation_id,
                session_id=session_id,
                source_message_ids=source_message_ids or [],
            )
        )

    return queue_record_fn
