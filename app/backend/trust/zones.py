"""Trust zone configuration for tools.

Provides static GREEN/YELLOW/RED zone classification with
automatic RED for outbound operations. Includes intent detection
to bypass confirmation when the user explicitly requested an action.
"""

import logging
import re
from enum import StrEnum
from typing import Any


logger = logging.getLogger(__name__)


class TrustZone(StrEnum):
    """Trust zone classification for tools.

    GREEN: Executes silently, no user notification.
    YELLOW: Executes immediately, user notified after.
    RED: Requires explicit user confirmation before execution.
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


# Default zone mappings - can be overridden
_DEFAULT_ZONES: dict[str, TrustZone] = {
    "file_read": TrustZone.GREEN,
    "file_search": TrustZone.GREEN,
    "memory_recall": TrustZone.GREEN,
    "system_status": TrustZone.GREEN,
    "skill_create": TrustZone.GREEN,
    "skill_search": TrustZone.GREEN,
    "skill_load": TrustZone.GREEN,
    "file_write": TrustZone.YELLOW,
    "schedule_task": TrustZone.YELLOW,
    "web_search": TrustZone.YELLOW,
    "http_fetch": TrustZone.RED,
    "email_send": TrustZone.RED,
}

_OUTBOUND_TOOLS: frozenset[str] = frozenset({"http_fetch", "email_send"})


def _zone_mapping(
    custom_zones: dict[str, TrustZone] | None = None,
) -> dict[str, TrustZone]:
    """Return the active tool-to-zone mapping."""
    return custom_zones or _DEFAULT_ZONES


def _split_target(
    zone: TrustZone,
    immediate: list[dict[str, Any]],
    confirmation: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the list that should receive one tool call for the zone."""
    return confirmation if zone == TrustZone.RED else immediate


def _tool_call_name(call: dict[str, Any]) -> str:
    """Return the requested tool name from one tool-call payload."""
    return str(call.get("name", ""))


def get_zone(
    tool_name: str,
    custom_zones: dict[str, TrustZone] | None = None,
) -> TrustZone:
    """Get the trust zone for a tool.

    Outbound tools are always RED regardless of configuration.

    Args:
        tool_name: Name of the tool.
        custom_zones: Optional custom zone mappings.

    Returns:
        Trust zone classification.
    """
    if tool_name in _OUTBOUND_TOOLS:
        return TrustZone.RED

    zones = _zone_mapping(custom_zones)
    return zones.get(tool_name, TrustZone.RED)


def is_outbound(tool_name: str) -> bool:
    """Check if a tool performs outbound operations."""
    return tool_name in _OUTBOUND_TOOLS


def list_tools_by_zone(
    zone: TrustZone,
    custom_zones: dict[str, TrustZone] | None = None,
) -> list[str]:
    """List all tools in a given trust zone."""
    zones = _zone_mapping(custom_zones)
    return [name for name, z in zones.items() if z == zone]


def split_by_zone(
    tool_calls: list[dict[str, Any]],
    custom_zones: dict[str, TrustZone] | None = None,
    user_intent_tools: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split tool calls into immediate and confirmation-required.

    Args:
        tool_calls: List of tool call dicts with 'name' key.
        custom_zones: Optional custom zone mappings.
        user_intent_tools: Tool names the user explicitly requested.
            RED tools in this set execute immediately.

    Returns:
        Tuple of (immediate_calls, confirmation_calls).
    """
    immediate: list[dict[str, Any]] = []
    confirmation: list[dict[str, Any]] = []
    intent_set = user_intent_tools or set()

    for call in tool_calls:
        tool_name = _tool_call_name(call)
        zone = get_zone(tool_name, custom_zones)
        if zone == TrustZone.RED and tool_name in intent_set:
            zone = TrustZone.YELLOW
        target = _split_target(zone, immediate, confirmation)
        target.append(call)

    return immediate, confirmation


_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

_INTENT_SIGNAL_MAP: dict[re.Pattern[str], str] = {
    _URL_PATTERN: "http_fetch",
    _EMAIL_PATTERN: "email_send",
}


def extract_user_intent_tools(message: str) -> set[str]:
    """Detect which RED tools the user explicitly requested.

    Scans the user message for intent signals (URLs, email addresses)
    and returns the set of tool names that should bypass confirmation.

    Args:
        message: Raw user message text.

    Returns:
        Set of tool names the user implicitly authorized.
    """
    intent_tools: set[str] = set()
    for pattern, tool_name in _INTENT_SIGNAL_MAP.items():
        if pattern.search(message):
            intent_tools.add(tool_name)
    return intent_tools


__all__ = [
    "TrustZone",
    "extract_user_intent_tools",
    "get_zone",
    "is_outbound",
    "list_tools_by_zone",
    "split_by_zone",
]
