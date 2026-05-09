"""Request body size limit middleware.

Rejects requests with Content-Length exceeding the configured
maximum (1MB). Returns HTTP 413 Payload Too Large for oversized
requests.
"""

import json
import logging
from typing import TYPE_CHECKING

from backend.middleware.responses import send_json_error


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 1_048_576  # 1 MB
MAX_BODY_SIZE_UPLOAD = 56_623_104  # ~54 MB (4 x 10MB images in base64 + text)

# Routes that accept large payloads (image uploads)
_LARGE_BODY_PATHS = {"/chat"}

TOO_LARGE_BODY = json.dumps({"detail": "Request body too large"}).encode()


class BodySizeMiddleware:
    """ASGI middleware that enforces request body size limits.

    Checks Content-Length header and rejects oversized requests
    before reading the body.
    """

    def __init__(self, app: ASGIApp, max_size: int = MAX_BODY_SIZE) -> None:
        """Initialize with ASGI app and size limit.

        Args:
            app: Next ASGI application in the chain.
            max_size: Maximum allowed body size in bytes.
        """
        self.app = app
        self.max_size = max_size

    def _limit_for_path(self, path: str) -> int:
        """Return the allowed request-body limit for one path."""
        if path in _LARGE_BODY_PATHS:
            return MAX_BODY_SIZE_UPLOAD
        return self.max_size

    @staticmethod
    def _is_http_scope(scope: Scope) -> bool:
        """Return whether this middleware call handles an HTTP request."""
        return scope["type"] == "http"

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        """Return the parsed Content-Length header when present."""
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is None:
            return None
        try:
            return int(content_length)
        except ValueError:
            return 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process the ASGI request.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if not self._is_http_scope(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        limit = self._limit_for_path(path)
        size = self._content_length(scope)
        if size is not None and size > limit:
            await self._send_413(send)
            return

        await self.app(scope, receive, send)

    async def _send_413(self, send: Send) -> None:
        """Send a 413 Payload Too Large response.

        Args:
            send: ASGI send callable.
        """
        await send_json_error(send, 413, TOO_LARGE_BODY)
