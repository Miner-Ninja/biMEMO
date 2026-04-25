"""Tests for trust zones."""

from backend.trust.zones import TrustZone, get_zone, is_outbound, split_by_zone


class TestTrustZone:
    """Tests for trust zone classification helpers."""

    def test_zone_values(self) -> None:
        """Enum values remain stable for policy configuration."""
        assert TrustZone.GREEN.value == "green"
        assert TrustZone.YELLOW.value == "yellow"
        assert TrustZone.RED.value == "red"

    def test_get_zone_green_tools(self) -> None:
        """Low-risk tools map to the green trust zone."""
        assert get_zone("file_read") == TrustZone.GREEN
        assert get_zone("file_search") == TrustZone.GREEN
        assert get_zone("system_status") == TrustZone.GREEN

    def test_get_zone_yellow_tools(self) -> None:
        """Medium-risk tools map to the yellow trust zone."""
        assert get_zone("file_write") == TrustZone.YELLOW
        assert get_zone("schedule_task") == TrustZone.YELLOW
        assert get_zone("web_search") == TrustZone.YELLOW

    def test_get_zone_red_tools(self) -> None:
        """Outbound tools map to the red trust zone."""
        assert get_zone("http_fetch") == TrustZone.RED
        assert get_zone("email_send") == TrustZone.RED

    def test_get_zone_unknown_defaults_to_red(self) -> None:
        """Unknown tool names default to the safest zone."""
        assert get_zone("unknown_tool") == TrustZone.RED

    def test_get_zone_custom_zones(self) -> None:
        """Custom zone overrides apply to non-outbound tools."""
        custom = {"my_tool": TrustZone.GREEN}
        assert get_zone("my_tool", custom) == TrustZone.GREEN

    def test_get_zone_custom_overrides_built_in_outbound(self) -> None:
        """Custom mapping wins for outbound tools too — profile is source of truth."""
        custom = {"email_send": TrustZone.GREEN}
        assert get_zone("email_send", custom) == TrustZone.GREEN

    def test_is_outbound(self) -> None:
        """Outbound classifier flags only explicitly external tools."""
        assert is_outbound("http_fetch") is True
        assert is_outbound("email_send") is True
        assert is_outbound("file_read") is False
        assert is_outbound("unknown") is False


class TestSplitByZone:
    """Tests for splitting tool calls into immediate and confirm groups."""

    def test_all_green(self) -> None:
        """Green calls execute immediately without confirmation."""
        calls = [
            {"name": "file_read", "params": {}},
            {"name": "file_search", "params": {}},
        ]
        immediate, confirm = split_by_zone(calls)
        assert len(immediate) == 2
        assert len(confirm) == 0

    def test_all_red(self) -> None:
        """Red calls are routed to the confirmation bucket."""
        calls = [
            {"name": "email_send", "params": {}},
            {"name": "http_fetch", "params": {}},
        ]
        immediate, confirm = split_by_zone(calls)
        assert len(immediate) == 0
        assert len(confirm) == 2

    def test_mixed_zones(self) -> None:
        """Mixed batches split according to each tool's trust zone."""
        calls = [
            {"name": "file_read", "params": {}},
            {"name": "email_send", "params": {}},
            {"name": "web_search", "params": {}},
        ]
        immediate, confirm = split_by_zone(calls)
        assert len(immediate) == 2
        assert len(confirm) == 1

    def test_empty_calls(self) -> None:
        """Empty batches return two empty result lists."""
        immediate, confirm = split_by_zone([])
        assert immediate == []
        assert confirm == []
