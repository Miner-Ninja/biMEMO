"""Application state check middleware.

Blocks requests to protected routes when the application is not
in READY state. SETUP state allows /health and /setup only.
LOCKED state allows /health and /unlock only.
"""

import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from backend.app_state import AppState, AppStateManager
from backend.middleware.responses import send_json_error


logger = logging.getLogger(__name__)

_STATIC = frozenset({"/", "/favicon.ico"})
_STATIC_PREFIXES = ("/assets/",)
SETUP_ALLOWED = frozenset({"/health", "/setup"})
LOCKED_ALLOWED = frozenset({"/health", "/unlock"})

SETUP_BODY = json.dumps({"detail": "System not configured"}).encode()
LOCKED_BODY = json.dumps({"detail": "System locked"}).encode()


def _is_http_scope(scope: Scope) -> bool:
    """Return whether this middleware call handles an HTTP request."""
    return scope["type"] == "http"


def _is_static_path(path: str) -> bool:
    """Return whether one request path should bypass state checks."""
    return path in _STATIC or any(
        path.startswith(prefix) for prefix in _STATIC_PREFIXES
    )


def _allowed_paths_for_state(state: AppState) -> frozenset[str]:
    """Return the allowed path set for one application state."""
    if state == AppState.SETUP:
        return SETUP_ALLOWED
    if state == AppState.LOCKED:
        return LOCKED_ALLOWED
    return frozenset()


def _blocked_state_body(state: AppState) -> bytes:
    """Return the response body for one blocked application state."""
    return SETUP_BODY if state == AppState.SETUP else LOCKED_BODY


class StateCheckMiddleware:
    """ASGI middleware that enforces app state restrictions.

    Returns 503 for protected routes when application is
    in SETUP or LOCKED state.
    """

    def __init__(self, app: ASGIApp, state_manager: AppStateManager) -> None:
        """Initialize with ASGI app and state manager.

        Args:
            app: Next ASGI application in the chain.
            state_manager: Application state manager.
        """
        self.app = app
        self.state_manager = state_manager

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
        if _is_static_path(path):
            await self.app(scope, receive, send)
            return

        state = self.state_manager.state

        allowed_paths = _allowed_paths_for_state(state)
        if allowed_paths and path not in allowed_paths:
            await self._send_503(send, _blocked_state_body(state))
            return

        await self.app(scope, receive, send)

    async def _send_503(self, send: Send, body: bytes) -> None:
        """Send a 503 Service Unavailable response.

        Args:
            send: ASGI send callable.
            body: Response body bytes.
        """
        await send_json_error(send, 503, body)
