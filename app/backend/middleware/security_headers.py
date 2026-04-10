"""Security headers middleware.

Adds security headers from the channels.web module to all
HTTP responses. Headers include CSP, X-Frame-Options,
X-Content-Type-Options, Referrer-Policy, and Permissions-Policy.
"""

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.channels.web import SECURITY_HEADERS


logger = logging.getLogger(__name__)

HEADER_PAIRS = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]


def _is_http_scope(scope: Scope) -> bool:
    """Return whether the ASGI scope is an HTTP request."""
    return scope["type"] == "http"


def _response_headers(message: Message) -> list[tuple[bytes, bytes]]:
    """Return a mutable response header list for one ASGI message."""
    return list(message.get("headers", []))


def _is_response_start(message: Message) -> bool:
    """Return whether one ASGI message starts an HTTP response."""
    return message["type"] == "http.response.start"


def _with_security_headers(message: Message) -> Message:
    """Return one response-start message with security headers appended."""
    headers = _response_headers(message)
    headers.extend(HEADER_PAIRS)
    message["headers"] = headers
    return message


class SecurityHeadersMiddleware:
    """ASGI middleware that adds security headers to responses.

    Injects standard security headers into every HTTP response.
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

        async def send_with_headers(message: Message) -> None:
            """Inject security headers into response start.

            Args:
                message: ASGI message.
            """
            if _is_response_start(message):
                message = _with_security_headers(message)
            await send(message)

        await self.app(scope, receive, send_with_headers)
