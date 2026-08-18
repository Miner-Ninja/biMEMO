"""JWT authentication middleware.

Extracts JWT access tokens from HTTP-only cookies, validates
them via SessionManager, and attaches claims to the request
scope. Returns 401 for missing or invalid tokens on protected
routes.
"""

import logging
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from backend.middleware.responses import send_json_error
from backend.processes.fastapi_app.problem import (
    SLUG_AUTHENTICATION_REQUIRED,
    problem_body,
)


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from backend.channels.jwt_auth import SessionManager


logger = logging.getLogger(__name__)

EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/auth/status",
        "/auth/setup",
        "/auth/login",
        "/refresh",
        "/bridge/providers/discord/messages",
        "/",
        "/setup",
        "/login",
        "/mcp",
    }
)
EXEMPT_PREFIXES = ("/assets/", "/mcp/")

UNAUTHORIZED_BODY = problem_body(
    status=401,
    type_slug=SLUG_AUTHENTICATION_REQUIRED,
    detail="Authentication required",
)


def _accepts_html(scope: Scope) -> bool:
    """Return whether one request prefers HTML responses."""
    headers = dict(scope.get("headers", []))
    accept = headers.get(b"accept", b"").decode()
    return "text/html" in accept


def _attach_claims(scope: Scope, claims: object) -> None:
    """Store validated claims on the ASGI scope."""
    scope.setdefault("state", {})
    scope["state"]["claims"] = claims


def _is_exempt_path(path: str) -> bool:
    """Return whether one request path can skip mandatory JWT auth."""
    return path in EXEMPT_PATHS or any(
        path.startswith(prefix) for prefix in EXEMPT_PREFIXES
    )


async def _pass_through_or_401(
    scope: Scope,
    receive: Receive,
    send: Send,
    app: ASGIApp,
) -> bool:
    """Allow HTML navigation through or send a 401 for API requests."""
    if _accepts_html(scope):
        await app(scope, receive, send)
        return True

    await send_json_error(send, 401, UNAUTHORIZED_BODY)
    return False


class JwtAuthMiddleware:
    """ASGI middleware for JWT cookie authentication.

    Skips authentication for exempt paths. Attaches validated
    TokenClaims to scope state for downstream handlers.

    Supports two modes:
    - signing_key: Direct Ed25519 key for synchronous verification
    - signing_client: Signing Proxy client for async IPC verification
    """

    def __init__(
        self,
        app: ASGIApp,
        session_manager: SessionManager,
        signing_key: object = None,
        signing_client: object = None,
        audit_fn: object = None,
    ) -> None:
        """Initialize with ASGI app and auth components.

        Args:
            app: Next ASGI application in the chain.
            session_manager: JWT session manager.
            signing_key: Ed25519 private key for direct verification.
            signing_client: Signing Proxy client for IPC verification.
            audit_fn: Optional async callable for token_revoked events.
        """
        self.app = app
        self.session_manager = session_manager
        self.signing_key = signing_key
        self.signing_client = signing_client
        self.audit_fn = audit_fn

    async def _validate(self, token: str) -> object:
        """Validate a token using signing_client or signing_key.

        Args:
            token: JWT string to validate.

        Returns:
            TokenClaims if valid, None otherwise.
        """
        if self.signing_client is not None:
            return await self.session_manager.async_validate_token(
                token,
                self.signing_client,
                audit_fn=self.audit_fn,
            )
        if self.signing_key is not None:
            return self.session_manager.validate_token(
                token,
                self.signing_key,
            )
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process the ASGI request.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if _is_exempt_path(path):
            token = self._extract_token(scope)
            if token:
                claims = await self._validate(token)
                if claims:
                    _attach_claims(scope, claims)
            await self.app(scope, receive, send)
            return

        token = self._extract_token(scope)
        if token is None:
            # Browser page navigation (Accept: text/html) without a token:
            # let it through so the SPA fallback can serve index.html.
            # React app handles auth check and redirects to /login.
            if await _pass_through_or_401(scope, receive, send, self.app):
                return
            return

        claims = await self._validate(token)
        if claims is None:
            if await _pass_through_or_401(scope, receive, send, self.app):
                return
            return

        _attach_claims(scope, claims)
        await self.app(scope, receive, send)

    def _extract_token(self, scope: Scope) -> str | None:
        """Extract access token from cookie header.

        Args:
            scope: ASGI connection scope.

        Returns:
            Token string or None if not found.
        """
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"")
        if not cookie_header:
            return None

        cookie = SimpleCookie(cookie_header.decode())
        morsel = cookie.get("access_token")
        if morsel is None:
            return None
        return morsel.value

    async def _send_401(self, send: Send) -> None:
        """Send a 401 Unauthorized response.

        Args:
            send: ASGI send callable.
        """
        await send_json_error(send, 401, UNAUTHORIZED_BODY)
