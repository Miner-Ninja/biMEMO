"""Telegram channel adapter.

Handles message routing, allowlist filtering, response
chunking, parameter masking, and trust confirmations.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from backend.channels.message_sanitizer import (
    ConfirmationMessage,
    format_confirmation,
    mask_body,
    mask_email,
    mask_path,
    mask_url,
)


logger = logging.getLogger(__name__)

# Re-export shared sanitizer symbols through the Telegram channel API.
__all__ = [
    "CREDENTIAL_LEAK_WARNING",
    "MAX_MESSAGE_LENGTH",
    "PRIVACY_WARNING",
    "STOP_CALLBACK_DATA",
    "ConfirmationMessage",
    "PrivacyState",
    "TelegramAdapter",
    "TelegramConfig",
    "ThinkingIndicator",
    "chunk_message",
    "format_confirmation",
    "is_allowed_user",
    "make_stop_keyboard",
    "mask_body",
    "mask_email",
    "mask_path",
    "mask_url",
]


class PrivacyState(StrEnum):
    """Telegram privacy acknowledgement state.

    NOT_ACKNOWLEDGED: User has not seen privacy warning.
    ACKNOWLEDGED: User has acknowledged the warning.
    """

    NOT_ACKNOWLEDGED = "not_acknowledged"
    ACKNOWLEDGED = "acknowledged"


# Telegram message size limit
MAX_MESSAGE_LENGTH = 4096

# Stop button callback data for inline keyboard
STOP_CALLBACK_DATA = "stop_generation"


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram adapter configuration.

    Attributes:
        bot_token: Telegram bot API token.
        allowed_user_ids: Set of allowed numeric user IDs.
    """

    bot_token: str
    allowed_user_ids: frozenset[int]


def is_allowed_user(user_id: int, config: TelegramConfig) -> bool:
    """Check if a user ID is in the allowlist.

    Args:
        user_id: Numeric Telegram user ID.
        config: Telegram configuration.

    Returns:
        True if user is allowed.
    """
    return user_id in config.allowed_user_ids


def chunk_message(text: str) -> list[str]:
    """Split a message into Telegram-compatible chunks.

    Splits at paragraph boundaries when possible,
    otherwise at the character limit.

    Args:
        text: Message text to chunk.

    Returns:
        List of message chunks.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break
        split_at = _chunk_split_index(text)
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _chunk_split_index(text: str) -> int:
    """Return the best split point for one oversized Telegram message chunk."""
    split_at = text.rfind("\n\n", 0, MAX_MESSAGE_LENGTH)
    if split_at == -1:
        split_at = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
    if split_at == -1:
        split_at = MAX_MESSAGE_LENGTH
    return split_at


PRIVACY_WARNING = (
    "\u26a0\ufe0f Telegram Privacy Notice:\n\n"
    "All messages sent via Telegram are stored on "
    "Telegram's servers outside the encrypted boundary. "
    "Sensitive parameters are partially masked, but "
    "full message history is accessible to Telegram.\n\n"
    "For maximum security, use the Web UI instead.\n\n"
    "Do you acknowledge and wish to continue?"
)

CREDENTIAL_LEAK_WARNING = (
    "\u26a0\ufe0f Credential detected in message. "
    "Messages sent via Telegram cannot be recalled "
    "from Telegram's servers."
)


def make_stop_keyboard() -> list[list[dict[str, str]]]:
    """Return inline keyboard data with a Stop button.

    Returns:
        Nested list representing one row with one button.
    """
    return [
        [
            {
                "text": "Stop",
                "callback_data": STOP_CALLBACK_DATA,
            }
        ]
    ]


@dataclass
class ThinkingIndicator:
    """Tracks thinking state for Telegram responses.

    Attributes:
        is_thinking: Whether the bot is processing.
        message_id: ID of the thinking message.
        chat_id: Telegram chat ID for the indicator.
    """

    is_thinking: bool = False
    message_id: int | None = None
    chat_id: int | None = None

    def start(self, message_id: int, chat_id: int) -> None:
        """Mark thinking started.

        Args:
            message_id: Telegram message ID of indicator.
            chat_id: Telegram chat ID of indicator.
        """
        self.is_thinking = True
        self.message_id = message_id
        self.chat_id = chat_id

    def stop(self) -> tuple[int | None, int | None]:
        """Mark thinking stopped.

        Returns:
            Tuple of (message_id, chat_id) to update.
        """
        msg_id = self.message_id
        c_id = self.chat_id
        self.is_thinking = False
        self.message_id = None
        self.chat_id = None
        return msg_id, c_id

    def handle_stop_callback(self, callback_data: str) -> bool:
        """Check if callback matches stop action.

        Args:
            callback_data: Callback data from button.

        Returns:
            True if active and callback matches.
        """
        return self.is_thinking and callback_data == STOP_CALLBACK_DATA


@dataclass
class TelegramAdapter:
    """Telegram channel adapter.

    Routes messages, manages confirmations, and
    handles per-user privacy acknowledgement state.

    Attributes:
        config: Telegram configuration.
        privacy_states: Per-user privacy warning state.
    """

    config: TelegramConfig
    privacy_states: dict[int, PrivacyState] = field(default_factory=dict)
    _pending_messages: dict[int, str] = field(default_factory=dict)
    _thinking: ThinkingIndicator = field(default_factory=ThinkingIndicator)

    def _is_allowed_user(self, user_id: int) -> bool:
        """Return whether one Telegram user is allowed to talk to the bot."""
        return is_allowed_user(user_id, self.config)

    def _has_privacy_acknowledgement(self, user_id: int) -> bool:
        """Return whether one user has already acknowledged the privacy warning."""
        return self.privacy_states.get(user_id) == PrivacyState.ACKNOWLEDGED

    def _queue_pending_message(self, user_id: int, text: str) -> str:
        """Store one pending Telegram message and return the privacy warning."""
        self._pending_messages[user_id] = text
        return PRIVACY_WARNING

    def handle_message(self, user_id: int, text: str) -> str | None:
        """Route an incoming Telegram message.

        Args:
            user_id: Sender's Telegram user ID.
            text: Message text.

        Returns:
            Response text, or None if denied.
        """
        if not self._is_allowed_user(user_id):
            return None
        return self._accept_message_text(user_id, text)

    def _accept_message_text(self, user_id: int, text: str) -> str:
        """Return either the queued privacy warning or the accepted user text."""
        if not self._has_privacy_acknowledgement(user_id):
            return self._queue_pending_message(user_id, text)
        return text

    def acknowledge_privacy(self, user_id: int) -> None:
        """Mark privacy warning as acknowledged for a user.

        Args:
            user_id: Numeric Telegram user ID.
        """
        self.privacy_states[user_id] = PrivacyState.ACKNOWLEDGED

    def pop_pending_message(self, user_id: int) -> str | None:
        """Retrieve and clear the queued message for a user.

        Args:
            user_id: Numeric Telegram user ID.

        Returns:
            Queued message text, or None if no message queued.
        """
        return self._pending_messages.pop(user_id, None)

    @property
    def thinking(self) -> ThinkingIndicator:
        """Return the thinking indicator."""
        return self._thinking
