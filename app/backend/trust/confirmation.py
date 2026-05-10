"""Confirmation flow for RED zone actions.

Generates cryptographic action IDs for single-use confirmations
with timeout and channel binding. The timeout and the
channel-binding toggle are sourced from AssistantPolicy, so policy
changes take effect without restarting the manager.
"""

import logging
import secrets
import time
from dataclasses import dataclass

from backend.security.policy import get_policy


logger = logging.getLogger(__name__)

# --- Catalog keys read from AssistantPolicy ---
_KEY_CONFIRMATION_TIMEOUT_SECONDS = "trust.confirmation_timeout_seconds"
_KEY_CONFIRMATION_CHANNEL_BINDING = "trust.confirmation_channel_binding"


@dataclass(frozen=True)
class PendingAction:
    """A RED zone action awaiting user confirmation.

    Attributes:
        action_id: Cryptographic action identifier.
        tool_name: Name of the tool to execute.
        parameters: Tool parameters (for display).
        channel: Channel where confirmation was requested.
        created_at: Monotonic timestamp of creation.
    """

    action_id: str
    tool_name: str
    parameters: dict
    channel: str
    created_at: float


class ConfirmationManager:
    """Manages pending RED zone confirmations.

    Each confirmation is single-use, channel-bound,
    and expires after timeout.

    Example:
        manager = ConfirmationManager(timeout=300)

        # Request confirmation for RED zone action
        action = manager.request_confirmation(
            tool_name="email_send",
            parameters={"to": "user@example.com", "body": "Hello"},
            channel="telegram"
        )

        # Later, approve the action
        confirmed = manager.approve(action.action_id, channel="telegram")
        if confirmed:
            # Execute the action
            pass
    """

    def __init__(
        self,
        timeout: float | None = None,
        channel_binding: bool | None = None,
    ) -> None:
        """Initialize the manager.

        Args:
            timeout: Optional fixed timeout in seconds. When omitted
                (the production path), the timeout is read fresh from
                the assistant policy on every check. Tests pass
                an explicit value to avoid wiring up a resolver.
            channel_binding: Optional fixed channel-binding flag. When
                omitted, the value is read from AssistantPolicy.
        """
        self._timeout_override = timeout
        self._channel_binding_override = channel_binding
        self._pending: dict[str, PendingAction] = {}

    @property
    def timeout(self) -> float:
        """Return the effective confirmation timeout in seconds."""
        if self._timeout_override is not None:
            return self._timeout_override
        return float(get_policy().get_int(_KEY_CONFIRMATION_TIMEOUT_SECONDS))

    @property
    def _channel_binding_enabled(self) -> bool:
        """Return whether channel-binding enforcement is currently active."""
        if self._channel_binding_override is not None:
            return self._channel_binding_override
        return get_policy().get_bool(_KEY_CONFIRMATION_CHANNEL_BINDING)

    def _now(self) -> float:
        """Return the current monotonic time."""
        return time.monotonic()

    def _action_prefix(self, action_id: str) -> str:
        """Return the short log-friendly action identifier prefix."""
        return action_id[:8]

    def _build_pending_action(
        self,
        tool_name: str,
        parameters: dict,
        channel: str,
    ) -> PendingAction:
        """Create one new pending action object."""
        return PendingAction(
            action_id=secrets.token_urlsafe(32),
            tool_name=tool_name,
            parameters=parameters,
            channel=channel,
            created_at=self._now(),
        )

    def _is_expired(self, action: PendingAction) -> bool:
        """Return whether one pending action is past the timeout."""
        return self._now() - action.created_at > self.timeout

    def _expired_action_ids(self) -> list[str]:
        """Return all expired pending action IDs."""
        return [
            action_id
            for action_id, action in self._pending.items()
            if self._is_expired(action)
        ]

    def _consume_pending_action(
        self,
        action_id: str,
    ) -> PendingAction | None:
        """Return and consume one pending action."""
        return self._pending.pop(action_id, None)

    def request_confirmation(
        self,
        tool_name: str,
        parameters: dict,
        channel: str,
    ) -> PendingAction:
        """Create a new confirmation request.

        Args:
            tool_name: Name of the tool.
            parameters: Tool call parameters.
            channel: Channel requesting confirmation.

        Returns:
            PendingAction with cryptographic action ID.
        """
        action = self._build_pending_action(tool_name, parameters, channel)
        self._pending[action.action_id] = action
        logger.info(
            "Confirmation requested: %s for %s",
            self._action_prefix(action.action_id),
            tool_name,
        )
        return action

    def approve(self, action_id: str, channel: str) -> PendingAction | None:
        """Approve a pending confirmation.

        The action is consumed (single-use) and must
        match the originating channel.

        Args:
            action_id: Action ID to approve.
            channel: Channel sending the approval.

        Returns:
            PendingAction if valid, None if invalid/expired.
        """
        action = self._consume_pending_action(action_id)
        if action is None:
            logger.warning("Unknown action ID: %s", self._action_prefix(action_id))
            return None
        if self._channel_binding_enabled and action.channel != channel:
            logger.warning(
                "Channel mismatch: expected %s, got %s",
                action.channel,
                channel,
            )
            return None
        elapsed = self._now() - action.created_at
        if elapsed > self.timeout:
            logger.warning(
                "Action %s expired (%.0fs)",
                self._action_prefix(action_id),
                elapsed,
            )
            return None
        return action

    def deny(self, action_id: str) -> bool:
        """Deny and remove a pending confirmation.

        Args:
            action_id: Action ID to deny.

        Returns:
            True if the action was found and removed.
        """
        removed = self._pending.pop(action_id, None)
        if removed:
            logger.info("Action denied: %s", self._action_prefix(action_id))
        return removed is not None

    def prune_expired(self) -> int:
        """Remove all expired pending actions.

        Returns:
            Number of expired actions removed.
        """
        expired = self._expired_action_ids()
        for aid in expired:
            del self._pending[aid]
        if expired:
            logger.info("Pruned %d expired confirmations", len(expired))
        return len(expired)

    @property
    def pending_count(self) -> int:
        """Return number of pending confirmations."""
        return len(self._pending)

    def get_pending(self) -> list[PendingAction]:
        """Return all pending actions (for display)."""
        return list(self._pending.values())


__all__ = ["ConfirmationManager", "PendingAction"]
