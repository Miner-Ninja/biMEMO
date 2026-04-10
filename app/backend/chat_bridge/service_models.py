"""Data models and exceptions for the chat bridge service layer.

Defines InboundBridgeSyncResult for tracking inbound bridge
message processing results, and ChatBridgeSyncSkippedError
for signaling expected bridge skip conditions. Separated from
service.py to allow importing types without loading the full
service class.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundBridgeSyncResult:
    """Stored result of one inbound bridge message."""

    conversation_id: str
    message_id: str
    assistant_content: str = ""
    assistant_message_id: str = ""


class ChatBridgeSyncSkippedError(RuntimeError):
    """Signal one expected bridge skip without treating it as a system failure."""

    def __init__(self, reason: str) -> None:
        """Store the skip reason for the caller."""
        super().__init__(reason)
        self.reason = reason
