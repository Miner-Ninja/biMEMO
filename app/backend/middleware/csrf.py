"""CSRF protection middleware.

Validates X-CSRF-Token header on state-changing requests
(POST, PUT, DELETE). Skips GET, HEAD, OPTIONS and exempt
paths.
"""

import logging
from typing import TYPE_CHECKING

from backend.channels.jwt_auth import verify_csrf
from backend.middleware.responses import send_json_error
from backend.processes.fastapi_app.problem import (
    SLUG_CSRF_FAILED,
    problem_body,
)


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from backend.channels.jwt_auth_models import TokenClaims


logger = logging.getLogger(__name__)

EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/auth/status",
        "/auth/setup",
        "/auth/login",
        "/refresh",
        "/bridge/providers/discord/messages",
        "/mcp",
    }
)
SAFE_METHODS = frozenset({b"GET", b"HEAD", b"OPTIONS"})

FORBIDDEN_BODY = problem_body(
    status=403,
    type_slug=SLUG_CSRF_FAILED,
    detail="CSRF validation failed",
)


def _is_http_scope(scope: Scope) -> bool:
    """Return whether this middleware call handles an HTTP request."""
    return scope["type"] == "http"


def _should_skip_csrf(method: bytes, path: str) -> bool:
    """Return whether one request can bypass CSRF validation."""
    return method in SAFE_METHODS or path in EXEMPT_PATHS


def _csrf_token(scope: Scope) -> str:
    """Read the incoming CSRF token header."""
    headers = dict(scope.get("headers", []))
    return headers.get(b"x-csrf-token", b"").decode()


def _claims_from_scope(scope: Scope) -> TokenClaims | None:
    """Read authenticated claims from request state."""
    return scope.get("state", {}).get("claims")


async def _audit_csrf_rejection(scope: Scope, audit_fn: object) -> None:
    """Schedule an audit event for a rejected CSRF check."""
    if audit_fn is None:
        return

    import asyncio

    audit_task = asyncio.ensure_future(audit_fn("csrf_rejected"))
    scope.setdefault("state", {})["_csrf_audit_task"] = audit_task


class CsrfMiddleware:
    """ASGI middleware for CSRF token validation.

    Validates X-CSRF-Token header against the expected token
    stored in the request scope state.
    """

    def __init__(
        self,
        app: ASGIApp,
        get_expected_token: object,
        audit_fn: object = None,
    ) -> None:
        """Initialize with ASGI app and token provider.

        Args:
            app: Next ASGI application in the chain.
            get_expected_token: Callable that returns expected CSRF token
                for a given session JTI, or None if not available.
            audit_fn: Optional async callable for security event audit.
        """
        self.app = app
        self.get_expected_token = get_expected_token
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

        method = scope.get("method", "GET").encode()
        path = scope["path"]
        if _should_skip_csrf(method, path):
            await self.app(scope, receive, send)
            return

        token = _csrf_token(scope)
        claims = _claims_from_scope(scope)
        if claims is None:
            await self.app(scope, receive, send)
            return

        expected = self.get_expected_token(claims.jti)
        if expected is None or not verify_csrf(token, expected):
            await _audit_csrf_rejection(scope, self.audit_fn)
            await self._send_403(send)
            return

        await self.app(scope, receive, send)

    async def _send_403(self, send: Send) -> None:
        """Send a 403 Forbidden response.

        Args:
            send: ASGI send callable.
        """
        await send_json_error(send, 403, FORBIDDEN_BODY)
