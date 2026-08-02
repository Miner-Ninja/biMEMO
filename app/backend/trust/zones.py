"""Trust zone configuration for tools.

Classifies each tool into GREEN/YELLOW/RED based on the active
assistant policy's `trust.red_zone_tools` and
`trust.yellow_zone_tools` lists. Outbound tools are tracked
separately as a fixed capability set so callers can detect
network-bound operations regardless of zone.
"""

import logging
import re
from enum import StrEnum
from typing import Any

from backend.security.policy import get_policy


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


# --- Tools whose effect is observable outside the host ---
_OUTBOUND_TOOLS: frozenset[str] = frozenset({"http_fetch", "email_send"})

# --- Policy keys ---
_KEY_RED_ZONE_TOOLS = "trust.red_zone_tools"
_KEY_YELLOW_ZONE_TOOLS = "trust.yellow_zone_tools"


def _policy_zone_mapping() -> dict[str, TrustZone]:
    """Build the tool-to-zone mapping from the assistant policy."""
    resolver = get_policy()
    mapping: dict[str, TrustZone] = {}
    for tool in resolver.get_list_str(_KEY_YELLOW_ZONE_TOOLS):
        mapping[tool] = TrustZone.YELLOW
    for tool in resolver.get_list_str(_KEY_RED_ZONE_TOOLS):
        mapping[tool] = TrustZone.RED
    return mapping


def _zone_mapping(
    custom_zones: dict[str, TrustZone] | None = None,
) -> dict[str, TrustZone]:
    """Return the active tool-to-zone mapping."""
    if custom_zones is not None:
        return custom_zones
    return _policy_zone_mapping()


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
    """Get the trust zone for a tool from the assistant policy.

    Args:
        tool_name: Name of the tool.
        custom_zones: Optional override mapping (used by tests).

    Returns:
        Trust zone classification. Tools not present in either
        the YELLOW or RED list default to GREEN. Unknown tools
        always default to RED for safety; the override mapping
        retains this guarantee for test inputs.
    """
    if custom_zones is not None:
        return custom_zones.get(tool_name, TrustZone.RED)

    zones = _policy_zone_mapping()
    if tool_name in zones:
        return zones[tool_name]
    if tool_name in _OUTBOUND_TOOLS:
        return TrustZone.RED
    # Known greens: fall through to GREEN; unknown defaults to RED.
    if _is_known_green_tool(tool_name):
        return TrustZone.GREEN
    return TrustZone.RED


_KNOWN_GREEN_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "file_search",
        "memory_recall",
        "system_status",
        "skill_create",
        "skill_search",
        "skill_load",
    },
)


def _is_known_green_tool(tool_name: str) -> bool:
    """Return whether one tool name is in the canonical GREEN set."""
    return tool_name in _KNOWN_GREEN_TOOLS


def is_outbound(tool_name: str) -> bool:
    """Check if a tool performs outbound operations."""
    return tool_name in _OUTBOUND_TOOLS


def list_tools_by_zone(
    zone: TrustZone,
    custom_zones: dict[str, TrustZone] | None = None,
) -> list[str]:
    """List all known tools in a given trust zone.

    The result merges AssistantPolicy red/yellow lists with
    the canonical GREEN tool set so callers can enumerate every
    well-known tool (not only those explicitly mapped).

    Args:
        zone: Target zone.
        custom_zones: Optional override mapping (used by tests).

    Returns:
        Tool names whose zone matches ``zone`` under the active
        configuration.
    """
    zones = _zone_mapping(custom_zones)
    explicit = [name for name, z in zones.items() if z == zone]
    if zone is TrustZone.GREEN:
        explicit.extend(tool for tool in _KNOWN_GREEN_TOOLS if zones.get(tool) is None)
    return explicit


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
