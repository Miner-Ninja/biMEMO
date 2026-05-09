"""Tests for static trust zone configuration.

Covers zone assignment, outbound classification,
and tool listing by zone.
"""

from backend.trust.zones import (
    TrustZone,
    extract_user_intent_tools,
    get_zone,
    is_outbound,
    list_tools_by_zone,
    split_by_zone,
)


class TestGetZone:
    """Tests for zone lookup."""

    def test_green_tool(self):
        """Verify GREEN zone for file_read."""
        assert get_zone("file_read") == TrustZone.GREEN

    def test_yellow_tool(self):
        """Verify YELLOW zone for file_write."""
        assert get_zone("file_write") == TrustZone.YELLOW

    def test_red_tool(self):
        """Verify RED zone for email_send."""
        assert get_zone("email_send") == TrustZone.RED

    def test_unknown_defaults_to_red(self):
        """Verify unknown tools default to RED."""
        assert get_zone("unknown_tool") == TrustZone.RED

    def test_fetch_is_yellow_in_single_assistant_policy(self):
        """Verify http_fetch runs immediately with notification."""
        assert get_zone("http_fetch") == TrustZone.YELLOW


class TestIsOutbound:
    """Tests for outbound classification."""

    def test_http_fetch_is_outbound(self):
        """Verify http_fetch is outbound."""
        assert is_outbound("http_fetch")

    def test_email_send_is_outbound(self):
        """Verify email_send is outbound."""
        assert is_outbound("email_send")

    def test_file_read_not_outbound(self):
        """Verify file_read is not outbound."""
        assert not is_outbound("file_read")

    def test_web_search_not_outbound(self):
        """Verify web_search is not outbound."""
        assert not is_outbound("web_search")


class TestListToolsByZone:
    """Tests for listing tools by zone."""

    def test_green_tools(self):
        """Verify GREEN tools listed."""
        tools = list_tools_by_zone(TrustZone.GREEN)
        assert "file_read" in tools
        assert "file_search" in tools

    def test_yellow_tools(self):
        """Verify YELLOW tools listed."""
        tools = list_tools_by_zone(TrustZone.YELLOW)
        assert "file_write" in tools
        assert "web_search" in tools
        assert "http_fetch" in tools

    def test_red_tools_include_confirmed_actions(self):
        """Verify RED includes actions that still need confirmation."""
        tools = list_tools_by_zone(TrustZone.RED)
        assert "email_send" in tools

    def test_fetch_not_in_green(self):
        """Verify http_fetch is not silent."""
        green = list_tools_by_zone(TrustZone.GREEN)
        assert "http_fetch" not in green


class TestExtractUserIntentTools:
    """Tests for intent signal extraction from user messages."""

    def test_http_url_returns_http_fetch(self):
        """Verify HTTP URL yields http_fetch intent."""
        result = extract_user_intent_tools("Check http://example.com please")
        assert result == {"http_fetch"}

    def test_https_url_returns_http_fetch(self):
        """Verify HTTPS URL yields http_fetch intent."""
        result = extract_user_intent_tools("Open https://example.com/page")
        assert result == {"http_fetch"}

    def test_email_returns_email_send(self):
        """Verify email address yields email_send intent."""
        result = extract_user_intent_tools("Send it to user@example.com")
        assert result == {"email_send"}

    def test_url_and_email_returns_both(self):
        """Verify message with URL and email yields both intents."""
        result = extract_user_intent_tools(
            "Fetch https://api.example.com and mail user@example.com"
        )
        assert result == {"http_fetch", "email_send"}

    def test_no_signals_returns_empty(self):
        """Verify message without signals yields empty set."""
        result = extract_user_intent_tools("Just a regular question")
        assert result == set()

    def test_plain_text_without_urls_returns_empty(self):
        """Verify plain text with no URLs or emails yields empty set."""
        result = extract_user_intent_tools("Tell me about the weather today")
        assert result == set()


class TestSplitByZoneWithIntent:
    """Tests for split_by_zone with user_intent_tools parameter."""

    def test_red_tool_in_intent_goes_to_immediate(self):
        """Verify RED tool matching user intent executes immediately."""
        calls = [{"name": "email_send", "params": {}}]
        immediate, confirm = split_by_zone(
            calls,
            user_intent_tools={"email_send"},
        )
        assert len(immediate) == 1
        assert len(confirm) == 0

    def test_red_tool_not_in_intent_goes_to_confirmation(self):
        """Verify RED tool without user intent requires confirmation."""
        calls = [{"name": "email_send", "params": {}}]
        immediate, confirm = split_by_zone(
            calls,
            user_intent_tools={"http_fetch"},
        )
        assert len(immediate) == 0
        assert len(confirm) == 1

    def test_green_tool_unaffected_by_intent(self):
        """Verify GREEN tool stays immediate regardless of intent."""
        calls = [{"name": "file_read", "params": {}}]
        immediate, confirm = split_by_zone(
            calls,
            user_intent_tools={"http_fetch"},
        )
        assert len(immediate) == 1
        assert len(confirm) == 0

    def test_yellow_tool_unaffected_by_intent(self):
        """Verify YELLOW tool stays immediate regardless of intent."""
        calls = [{"name": "file_write", "params": {}}]
        immediate, confirm = split_by_zone(
            calls,
            user_intent_tools={"http_fetch"},
        )
        assert len(immediate) == 1
        assert len(confirm) == 0

    def test_empty_intent_same_as_no_intent(self):
        """Verify empty intent set behaves like no intent parameter."""
        calls = [
            {"name": "file_read", "params": {}},
            {"name": "http_fetch", "params": {}},
        ]
        immediate_empty, confirm_empty = split_by_zone(
            calls,
            user_intent_tools=set(),
        )
        immediate_none, confirm_none = split_by_zone(calls)
        assert len(immediate_empty) == len(immediate_none)
        assert len(confirm_empty) == len(confirm_none)
