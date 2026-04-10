"""Web UI channel endpoints.

Provides REST endpoints for stop, confirm, and tool
status, plus HTTP security headers.
"""

import logging
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

# Security headers for all responses
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": ("camera=(), microphone=(self), geolocation=()"),
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "media-src 'self' blob:; "
        "connect-src 'self' blob:"
    ),
}


@dataclass
class ToolStatus:
    """Current tool execution status.

    Attributes:
        running_tools: Names of currently executing tools.
    """

    running_tools: list[str] = field(default_factory=list)

    def _tool_names(self) -> list[str]:
        """Return the mutable list of running tool names."""
        return self.running_tools

    def _contains(self, tool_name: str) -> bool:
        """Return whether one tool is already marked as running."""
        return tool_name in self._tool_names()

    def _running_snapshot(self) -> list[str]:
        """Return a copy of the currently running tool names."""
        return list(self._tool_names())

    def add(self, tool_name: str) -> None:
        """Mark a tool as running.

        Args:
            tool_name: Name of the tool.
        """
        if not self._contains(tool_name):
            self._tool_names().append(tool_name)

    def remove(self, tool_name: str) -> None:
        """Mark a tool as no longer running.

        Args:
            tool_name: Name of the tool.
        """
        if self._contains(tool_name):
            self._tool_names().remove(tool_name)

    def to_dict(self) -> dict:
        """Return status as a dictionary.

        Returns:
            Dict with running tool names.
        """
        return {"running": self._running_snapshot()}
