"""Discord channel adapter.

Handles message routing, allowlist filtering, response
chunking (2000 char limit), and trust confirmations
for the Discord DM bot integration and bridge-channel intake.
"""

import collections
import logging
from dataclasses import dataclass, field
from enum import StrEnum


logger = logging.getLogger(__name__)

# Discord message size limit
MAX_MESSAGE_LENGTH = 2000
DEFAULT_BRIDGE_INBOUND_TIMEOUT_SECONDS = 10.0


class PrivacyState(StrEnum):
    """Discord privacy acknowledgement state.

    NOT_ACKNOWLEDGED: User has not seen privacy warning.
    ACKNOWLEDGED: User has acknowledged the warning.
    """

    NOT_ACKNOWLEDGED = "not_acknowledged"
    ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class DiscordConfig:
    """Discord adapter configuration.

    Attributes:
        bot_token: Discord bot API token.
        allowed_user_ids: Set of allowed numeric Discord user IDs.
        bridge_category_id: Optional Discord category ID for bridge channels.
        bridge_external_space_id: Optional guild or provider scope ID for bridge
            channel routing.
        bridge_inbound_url: Optional FastAPI route base URL for bridge intake.
        bridge_inbound_token: Optional bearer token for bridge intake auth.
    """

    bot_token: str
    allowed_user_ids: frozenset[int]
    bridge_category_id: str = ""
    bridge_external_space_id: str = ""
    bridge_inbound_url: str = ""
    bridge_inbound_token: str = ""


def is_allowed_user(user_id: int, config: DiscordConfig) -> bool:
    """Check if a user ID is in the allowlist.

    Args:
        user_id: Numeric Discord user ID.
        config: Discord configuration.

    Returns:
        True if user is allowed.
    """
    return user_id in config.allowed_user_ids


def _privacy_state(
    privacy_states: dict[int, PrivacyState],
    user_id: int,
) -> PrivacyState:
    """Return one user's current privacy acknowledgement state."""
    return privacy_states.get(user_id, PrivacyState.NOT_ACKNOWLEDGED)


def chunk_message(text: str) -> list[str]:
    """Split a message into Discord-compatible chunks.

    Discord limits messages to 2000 characters.
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
    """Return the best split point for one oversized Discord message chunk."""
    split_at = text.rfind("\n\n", 0, MAX_MESSAGE_LENGTH)
    if split_at == -1:
        split_at = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
    if split_at == -1:
        split_at = MAX_MESSAGE_LENGTH
    return split_at


PRIVACY_WARNING = (
    "\u26a0\ufe0f **Discord Privacy Notice**\n\n"
    "All messages sent via Discord are stored on "
    "Discord's servers outside the encrypted boundary. "
    "Sensitive parameters are partially masked, but "
    "full message history is accessible to Discord.\n\n"
    "For maximum security, use the Web UI instead.\n\n"
    "Do you acknowledge and wish to continue?"
)

CREDENTIAL_LEAK_WARNING = (
    "\u26a0\ufe0f Credential detected in message. "
    "Messages sent via Discord cannot be recalled "
    "from Discord's servers."
)


@dataclass
class DiscordAdapter:
    """Discord channel adapter.

    Routes messages, manages confirmations, and
    handles per-user privacy acknowledgement state.

    Attributes:
        config: Discord configuration.
        privacy_states: Per-user privacy warning state.
    """

    config: DiscordConfig
    privacy_states: dict[int, PrivacyState] = field(default_factory=dict)
    _pending_messages: dict[int, str] = field(default_factory=dict)
    _thinking_channels: set[int] = field(default_factory=set)
    _seen_message_ids: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=200),
    )

    def _is_allowed_user(self, user_id: int) -> bool:
        """Return whether one Discord user is allowed to talk to the bot."""
        return is_allowed_user(user_id, self.config)

    def _has_privacy_acknowledgement(self, user_id: int) -> bool:
        """Return whether one user has already acknowledged the privacy warning."""
        return _privacy_state(self.privacy_states, user_id) == PrivacyState.ACKNOWLEDGED

    def _queue_pending_message(self, user_id: int, text: str) -> str:
        """Store one pending user message and return the privacy warning."""
        self._pending_messages[user_id] = text
        return PRIVACY_WARNING

    def handle_message(self, user_id: int, text: str) -> str | None:
        """Route an incoming Discord message.

        Args:
            user_id: Sender's Discord user ID.
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
            user_id: Numeric Discord user ID.
        """
        self.privacy_states[user_id] = PrivacyState.ACKNOWLEDGED

    def pop_pending_message(self, user_id: int) -> str | None:
        """Retrieve and clear the queued message for a user.

        Args:
            user_id: Numeric Discord user ID.

        Returns:
            Queued message text, or None if no message queued.
        """
        return self._pending_messages.pop(user_id, None)

    def is_duplicate_message(self, message_id: int) -> bool:
        """Check if a message was already processed.

        Uses a bounded deque to track recent message IDs.
        Returns True if the message was already seen.

        Args:
            message_id: Discord message snowflake ID.

        Returns:
            True if duplicate (already seen).
        """
        if message_id in self._seen_message_ids:
            return True
        self._seen_message_ids.append(message_id)
        return False

    def mark_thinking(self, user_id: int) -> None:
        """Mark that a user's request is being processed.

        Args:
            user_id: Numeric Discord user ID.
        """
        self._thinking_channels.add(user_id)

    def clear_thinking(self, user_id: int) -> None:
        """Clear thinking state for a user.

        Args:
            user_id: Numeric Discord user ID.
        """
        self._thinking_channels.discard(user_id)

    def is_thinking(self, user_id: int) -> bool:
        """Check if a user's request is being processed.

        Args:
            user_id: Numeric Discord user ID.

        Returns:
            True if currently processing.
        """
        return user_id in self._thinking_channels
