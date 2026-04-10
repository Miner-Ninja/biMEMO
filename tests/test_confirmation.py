"""Tests for RED zone confirmation flow.

Covers action creation, approval, denial, timeout,
channel binding, and expiry pruning.
"""

import time

from backend.trust.confirmation import (
    ConfirmationManager,
)


class TestRequestConfirmation:
    """Tests for creating confirmation requests."""

    def test_creates_action(self):
        """Verify action is created with unique ID."""
        mgr = ConfirmationManager()
        action = mgr.request_confirmation("email_send", {"to": "a@b.com"}, "telegram")
        assert action.action_id
        assert action.tool_name == "email_send"
        assert action.channel == "telegram"
        assert mgr.pending_count == 1

    def test_unique_ids(self):
        """Verify each action gets a unique ID."""
        mgr = ConfirmationManager()
        a1 = mgr.request_confirmation("t", {}, "telegram")
        a2 = mgr.request_confirmation("t", {}, "telegram")
        assert a1.action_id != a2.action_id


class TestApprove:
    """Tests for approving confirmations."""

    def test_approve_valid(self):
        """Verify valid approval returns action."""
        mgr = ConfirmationManager()
        action = mgr.request_confirmation("email_send", {"to": "a@b.com"}, "telegram")
        result = mgr.approve(action.action_id, "telegram")
        assert result is not None
        assert result.tool_name == "email_send"
        assert mgr.pending_count == 0

    def test_approve_single_use(self):
        """Verify action consumed after approval."""
        mgr = ConfirmationManager()
        action = mgr.request_confirmation("t", {}, "telegram")
        mgr.approve(action.action_id, "telegram")
        result = mgr.approve(action.action_id, "telegram")
        assert result is None

    def test_approve_wrong_channel(self):
        """Verify wrong channel rejected."""
        mgr = ConfirmationManager()
        action = mgr.request_confirmation("t", {}, "telegram")
        result = mgr.approve(action.action_id, "web")
        assert result is None

    def test_approve_unknown_id(self):
        """Verify unknown ID returns None."""
        mgr = ConfirmationManager()
        result = mgr.approve("nonexistent", "telegram")
        assert result is None

    def test_approve_expired(self):
        """Verify expired action rejected."""
        mgr = ConfirmationManager(timeout=0.01)
        action = mgr.request_confirmation("t", {}, "telegram")
        time.sleep(0.02)
        result = mgr.approve(action.action_id, "telegram")
        assert result is None


class TestDeny:
    """Tests for denying confirmations."""

    def test_deny_existing(self):
        """Verify existing action denied."""
        mgr = ConfirmationManager()
        action = mgr.request_confirmation("t", {}, "telegram")
        assert mgr.deny(action.action_id)
        assert mgr.pending_count == 0

    def test_deny_nonexistent(self):
        """Verify denying nonexistent returns False."""
        mgr = ConfirmationManager()
        assert not mgr.deny("nonexistent")


class TestPruneExpired:
    """Tests for expired action pruning."""

    def test_prune_removes_expired(self):
        """Verify expired actions are pruned."""
        mgr = ConfirmationManager(timeout=0.01)
        mgr.request_confirmation("t", {}, "telegram")
        mgr.request_confirmation("t", {}, "telegram")
        time.sleep(0.02)
        count = mgr.prune_expired()
        assert count == 2
        assert mgr.pending_count == 0

    def test_prune_keeps_valid(self):
        """Verify non-expired actions kept."""
        mgr = ConfirmationManager(timeout=60)
        mgr.request_confirmation("t", {}, "telegram")
        count = mgr.prune_expired()
        assert count == 0
        assert mgr.pending_count == 1
