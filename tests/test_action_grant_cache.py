"""Tests for cached assistant action grants."""

from backend.permissions import ActionGrantCache


class TestActionGrantCache:
    """Verify action grant lookup precedence."""

    def test_conversation_channel_grant_matches_first(self):
        """Prefer conversation and channel-specific grants."""
        cache = ActionGrantCache(
            [
                {
                    "scope_type": "conversation",
                    "scope_value": "conv-1",
                    "capability": "http_fetch",
                    "decision": "allow",
                    "channel": "web",
                }
            ]
        )

        decision = cache.find_decision(
            conversation_id="conv-1",
            channel="web",
            tool_name="http_fetch",
        )

        assert decision == "allow"

    def test_global_grant_is_fallback(self):
        """Use a global grant when no conversation grant matches."""
        cache = ActionGrantCache()
        cache.remember(
            scope_type="global",
            scope_value="*",
            capability="http_fetch",
            decision="block",
        )

        decision = cache.find_decision(
            conversation_id="conv-1",
            channel="telegram",
            tool_name="http_fetch",
        )

        assert decision == "block"

    def test_missing_grant_returns_none(self):
        """Return None when no grant matches."""
        cache = ActionGrantCache()

        decision = cache.find_decision(
            conversation_id="conv-1",
            channel="web",
            tool_name="http_fetch",
        )

        assert decision is None
