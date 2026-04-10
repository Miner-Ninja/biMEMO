"""Tests for ``ConfirmationManager``."""

from backend.trust.confirmation import ConfirmationManager


class TestConfirmationManager:
    """Cover confirmation lifecycle operations and pending state tracking."""

    def test_request_confirmation(self) -> None:
        """Stores a pending action with the submitted tool metadata."""
        manager = ConfirmationManager(timeout=300)

        action = manager.request_confirmation(
            tool_name="email_send",
            parameters={"to": "test@example.com"},
            channel="web",
        )

        assert action.tool_name == "email_send"
        assert action.parameters == {"to": "test@example.com"}
        assert action.channel == "web"
        assert action.action_id is not None
        assert manager.pending_count == 1

    def test_approve_valid(self) -> None:
        """Approves a pending action when the channel matches."""
        manager = ConfirmationManager(timeout=300)

        action = manager.request_confirmation("email_send", {}, "telegram")
        approved_action = manager.approve(action.action_id, "telegram")

        assert approved_action is not None
        assert approved_action.tool_name == "email_send"
        assert manager.pending_count == 0

    def test_approve_wrong_channel(self) -> None:
        """Rejects approval when the action is confirmed from another channel."""
        manager = ConfirmationManager(timeout=300)

        action = manager.request_confirmation("email_send", {}, "telegram")
        approved_action = manager.approve(action.action_id, "web")

        assert approved_action is None
        assert manager.pending_count == 0

    def test_approve_unknown_id(self) -> None:
        """Returns ``None`` for an unknown action identifier."""
        manager = ConfirmationManager(timeout=300)

        result = manager.approve("unknown_id", "web")

        assert result is None

    def test_deny(self) -> None:
        """Removes a pending action when it is denied."""
        manager = ConfirmationManager(timeout=300)

        action = manager.request_confirmation("email_send", {}, "web")
        denied = manager.deny(action.action_id)

        assert denied is True
        assert manager.pending_count == 0

    def test_deny_unknown_id(self) -> None:
        """Returns ``False`` when denying a missing action."""
        manager = ConfirmationManager(timeout=300)

        result = manager.deny("unknown_id")

        assert result is False

    def test_pending_count(self) -> None:
        """Tracks the number of currently pending confirmation actions."""
        manager = ConfirmationManager(timeout=300)

        assert manager.pending_count == 0

        manager.request_confirmation("tool1", {}, "web")
        assert manager.pending_count == 1

        manager.request_confirmation("tool2", {}, "web")
        assert manager.pending_count == 2

    def test_get_pending(self) -> None:
        """Returns pending actions in insertion order."""
        manager = ConfirmationManager(timeout=300)

        manager.request_confirmation("email_send", {"to": "a@b.com"}, "web")
        manager.request_confirmation(
            "http_fetch",
            {"url": "http://x.com"},
            "telegram",
        )

        pending_actions = manager.get_pending()

        assert len(pending_actions) == 2
        assert pending_actions[0].tool_name == "email_send"
        assert pending_actions[1].tool_name == "http_fetch"
