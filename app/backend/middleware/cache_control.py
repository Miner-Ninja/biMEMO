"""Cache-Control policy middleware.

Stamps Cache-Control by resource class without overwriting a value the
route already chose (setdefault style):
- JSON API responses (application/json, application/problem+json) ->
  private, no-store;
- static assets: content-hashed dist files (Vite: <name>-<hash>.<ext>
  inside /assets/) -> public, max-age=31536000, immutable;
  index.html -> no-cache.
"""

import logging
import re
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger(__name__)

_JSON_MEDIA_TYPES = frozenset({"application/json", "application/problem+json"})
_INDEX_PATHS = frozenset({"/", "/index.html"})
_HASHED_ASSET_RE = re.compile(
    r"^/assets/[A-Za-z0-9._-]+-[0-9A-Za-z]{8,}\.[A-Za-z0-9]+$"
)

CACHE_CONTROL_JSON = "private, no-store"
CACHE_CONTROL_STATIC_HASHED = "public, max-age=31536000, immutable"
CACHE_CONTROL_INDEX = "no-cache"


def _is_http_scope(scope: Scope) -> bool:
    """Return whether the ASGI scope is an HTTP request."""
    return scope["type"] == "http"


def _is_response_start(message: Message) -> bool:
    """Return whether one ASGI message starts an HTTP response."""
    return message["type"] == "http.response.start"


def _header_value(message: Message, name: bytes) -> str | None:
    """Return the decoded value of one response header, or None."""
    for header_name, value in message.get("headers", []):
        if header_name.lower() == name:
            return value.decode("latin-1")
    return None


def _media_type(message: Message) -> str:
    """Return the lowercase media type of one response start message."""
    content_type = _header_value(message, b"content-type")
    if content_type is None:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _policy_for(path: str, message: Message) -> str | None:
    """Return the Cache-Control value for one response, or None."""
    if _media_type(message) in _JSON_MEDIA_TYPES:
        return CACHE_CONTROL_JSON
    if path in _INDEX_PATHS:
        return CACHE_CONTROL_INDEX
    if _HASHED_ASSET_RE.match(path):
        return CACHE_CONTROL_STATIC_HASHED
    return None


def _with_cache_control(message: Message, value: str) -> Message:
    """Return one response-start message with a Cache-Control header."""
    headers = list(message.get("headers", []))
    headers.append((b"cache-control", value.encode("utf-8")))
    message["headers"] = headers
    return message


class CacheControlMiddleware:
    """ASGI middleware that stamps Cache-Control by resource class.

    Setdefault style: a response that already carries a Cache-Control
    header (set by a route or an earlier middleware) is left unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize with ASGI app.

        Args:
            app: Next ASGI application in the chain.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process the ASGI request.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if not _is_http_scope(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_cache_control(message: Message) -> None:
            """Stamp the policy header onto one response start."""
            if (
                _is_response_start(message)
                and _header_value(message, b"cache-control") is None
            ):
                value = _policy_for(path, message)
                if value is not None:
                    message = _with_cache_control(message, value)
            await send(message)

        await self.app(scope, receive, send_with_cache_control)
