"""Per-endpoint rate limiting middleware.

Checks EndpointRateLimiter before allowing requests. Records
allowed requests and returns 429 with Retry-After header when
rate limit is exceeded.
"""

import logging
from typing import TYPE_CHECKING

from backend.middleware.responses import send_json_error
from backend.processes.fastapi_app.problem import (
    SLUG_RATE_LIMITED,
    problem_body,
)


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from backend.channels.endpoints import EndpointRateLimiter


logger = logging.getLogger(__name__)

RATE_LIMITED_BODY = problem_body(
    status=429,
    type_slug=SLUG_RATE_LIMITED,
    detail="Rate limit exceeded",
)


def _is_http_scope(scope: Scope) -> bool:
    """Return whether this middleware call handles an HTTP request."""
    return scope["type"] == "http"


def _endpoint_name(path: str) -> str:
    """Return the top-level endpoint name from one request path."""
    return path.lstrip("/").split("/")[0]


async def _audit_rate_limit(scope: Scope, audit_fn: object, endpoint: str) -> None:
    """Schedule one audit event for a blocked request."""
    if audit_fn is None:
        return

    import asyncio

    audit_task = asyncio.ensure_future(audit_fn("rate_limited", tool_name=endpoint))
    scope.setdefault("state", {})["_rate_limit_audit_task"] = audit_task


class RateLimitMiddleware:
    """ASGI middleware for per-endpoint rate limiting.

    Uses EndpointRateLimiter to track and enforce request
    rates per endpoint path.
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: EndpointRateLimiter,
        audit_fn: object = None,
    ) -> None:
        """Initialize with ASGI app and rate limiter.

        Args:
            app: Next ASGI application in the chain.
            limiter: Endpoint rate limiter instance.
            audit_fn: Optional async callable for security event audit.
        """
        self.app = app
        self.limiter = limiter
        self.audit_fn = audit_fn

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

        path = scope["path"]
        endpoint = _endpoint_name(path)

        if not self.limiter.is_allowed(endpoint):
            retry_after = self.limiter.retry_after(endpoint)
            await _audit_rate_limit(scope, self.audit_fn, endpoint)
            await self._send_429(send, retry_after)
            return

        self.limiter.record(endpoint)
        await self.app(scope, receive, send)

    async def _send_429(self, send: Send, retry_after: float) -> None:
        """Send a 429 Too Many Requests response.

        Args:
            send: ASGI send callable.
            retry_after: Seconds until next allowed request.
        """
        retry_str = str(int(retry_after + 1))
        await send_json_error(
            send,
            429,
            RATE_LIMITED_BODY,
            extra_headers=[[b"retry-after", retry_str.encode()]],
        )
