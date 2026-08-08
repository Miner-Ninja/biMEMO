"""Data models for the dialog recorder rate limiter queue.

Defines DialogRecorderQueueItem used by the bounded async queue
and coalescing logic. Separated from rate_limiter.py to allow
importing the queue item type without loading the full rate
limiter and queue implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DialogRecorderQueueItem:
    """Item in the dialog recorder processing queue.

    Attributes:
        messages: Message texts to compress.
        conversation_id: ID of the conversation.
        session_id: ID of the current session.
        source_message_ids: Source message IDs used for compression.
        injection_flagged: Whether the session has an
            active injection warning.
        queue_ids: IDs of the persisted record_queue rows backing this
            item (several after coalescing). Deleted once processed.
        attempts: How many times processing this item failed with a
            transient LLM error and was retried.
    """

    messages: list[str] = field(default_factory=list)
    conversation_id: str = ""
    session_id: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    injection_flagged: bool = False
    queue_ids: tuple[str, ...] = ()
    attempts: int = 0
