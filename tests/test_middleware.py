"""Tests for ASGI middleware stack.

Covers state check, JWT auth, CSRF, body size,
rate limiting, and security headers middleware.
"""

from unittest.mock import MagicMock

from backend.app_state import AppState, AppStateManager
from backend.channels.endpoints import EndpointRateLimiter
from backend.channels.jwt_auth import (
    SessionManager,
    derive_signing_key,
)
from backend.channels.web import SECURITY_HEADERS
from backend.middleware.body_size import BodySizeMiddleware
from backend.middleware.csrf import CsrfMiddleware
from backend.middleware.jwt_auth import JwtAuthMiddleware
from backend.middleware.rate_limit import RateLimitMiddleware
from backend.middleware.security_headers import (
    SecurityHeadersMiddleware,
)
from backend.middleware.state_check import StateCheckMiddleware
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _ok_handler(request):
    """Return 200 OK for test routes."""
    return JSONResponse({"ok": True})


def _make_key():
    """Create a test signing key."""
    return derive_signing_key(b"\x01" * 32, b"\x02" * 16)


class TestStateCheckMiddleware:
    """Tests for app state check middleware."""

    def _make_app(self, state: AppState):
        """Create test app with state check middleware."""
        mgr = AppStateManager(state)
        app = Starlette(
            routes=[
                Route("/health", _ok_handler),
                Route("/setup", _ok_handler, methods=["POST"]),
                Route("/unlock", _ok_handler, methods=["POST"]),
                Route("/chat", _ok_handler, methods=["POST"]),
                Route("/admin/integrations", _ok_handler),
            ],
        )
        app.add_middleware(StateCheckMiddleware, state_manager=mgr)
        return app

    def test_setup_allows_health(self):
        """Verify /health allowed in SETUP state."""
        client = TestClient(self._make_app(AppState.SETUP))
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_setup_allows_setup(self):
        """Verify /setup allowed in SETUP state."""
        client = TestClient(self._make_app(AppState.SETUP))
        resp = client.post("/setup")
        assert resp.status_code == 200

    def test_setup_blocks_chat(self):
        """Verify /chat blocked in SETUP state."""
        client = TestClient(self._make_app(AppState.SETUP))
        resp = client.post("/chat")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_setup_blocks_unlock(self):
        """Verify /unlock blocked in SETUP state."""
        client = TestClient(self._make_app(AppState.SETUP))
        resp = client.post("/unlock")
        assert resp.status_code == 503

    def test_locked_allows_health(self):
        """Verify /health allowed in LOCKED state."""
        client = TestClient(self._make_app(AppState.LOCKED))
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_locked_allows_unlock(self):
        """Verify /unlock allowed in LOCKED state."""
        client = TestClient(self._make_app(AppState.LOCKED))
        resp = client.post("/unlock")
        assert resp.status_code == 200

    def test_locked_blocks_chat(self):
        """Verify /chat blocked in LOCKED state."""
        client = TestClient(self._make_app(AppState.LOCKED))
        resp = client.post("/chat")
        assert resp.status_code == 503
        assert "locked" in resp.json()["detail"]

    def test_locked_blocks_admin(self):
        """Verify /admin blocked in LOCKED state."""
        client = TestClient(self._make_app(AppState.LOCKED))
        resp = client.get("/admin/integrations")
        assert resp.status_code == 503

    def test_ready_allows_all(self):
        """Verify all routes allowed in READY state."""
        client = TestClient(self._make_app(AppState.READY))
        assert client.get("/health").status_code == 200
        assert client.post("/chat").status_code == 200
        assert client.get("/admin/integrations").status_code == 200


class TestJwtAuthMiddleware:
    """Tests for JWT authentication middleware."""

    def _make_app(self):
        """Create test app with JWT middleware."""
        key = _make_key()
        mgr = SessionManager()
        token = mgr.issue_access_token(key)
        app = Starlette(
            routes=[
                Route("/health", _ok_handler),
                Route("/setup", _ok_handler, methods=["POST"]),
                Route("/auth/login", _ok_handler, methods=["POST"]),
                Route(
                    "/bridge/providers/discord/messages",
                    _ok_handler,
                    methods=["POST"],
                ),
                Route("/chat", _ok_handler, methods=["POST"]),
            ],
        )
        app.add_middleware(
            JwtAuthMiddleware,
            session_manager=mgr,
            signing_key=key,
        )
        return app, token

    def test_exempt_route_no_token(self):
        """Verify exempt routes skip auth."""
        app, _ = self._make_app()
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert client.post("/setup").status_code == 200
        assert client.post("/auth/login").status_code == 200
        assert client.post("/bridge/providers/discord/messages").status_code == 200

    def test_missing_token_returns_401(self):
        """Verify missing token returns 401."""
        app, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/chat")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        """Verify invalid token returns 401."""
        app, _ = self._make_app()
        client = TestClient(app, cookies={"access_token": "garbage"})
        resp = client.post("/chat")
        assert resp.status_code == 401

    def test_valid_token_passes(self):
        """Verify valid token allows request."""
        app, token = self._make_app()
        client = TestClient(app, cookies={"access_token": token})
        resp = client.post("/chat")
        assert resp.status_code == 200


class TestCsrfMiddleware:
    """Tests for CSRF validation middleware."""

    def _make_app(self):
        """Create test app with CSRF middleware."""
        csrf_tokens = {}

        def get_token(jti):
            return csrf_tokens.get(jti)

        app = Starlette(
            routes=[
                Route("/health", _ok_handler),
                Route("/auth/setup", _ok_handler, methods=["POST"]),
                Route("/auth/login", _ok_handler, methods=["POST"]),
                Route("/chat", _ok_handler, methods=["POST"]),
                Route("/data", _ok_handler),
            ],
        )
        app.add_middleware(CsrfMiddleware, get_expected_token=get_token)
        return app, csrf_tokens

    def test_get_request_skipped(self):
        """Verify GET requests skip CSRF check."""
        app, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/data")
        assert resp.status_code == 200

    def test_exempt_paths_skipped(self):
        """Verify exempt paths skip CSRF check."""
        app, _ = self._make_app()
        client = TestClient(app)
        assert client.post("/auth/setup").status_code == 200
        assert client.post("/auth/login").status_code == 200

    def test_no_claims_passes(self):
        """Verify request without claims passes CSRF."""
        app, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/chat")
        assert resp.status_code == 200

    def test_valid_csrf_passes(self):
        """Verify valid CSRF token passes."""
        app, tokens = self._make_app()
        tokens["test-jti"] = "valid-token"
        # Simulate claims in scope state
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectClaims(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.claims = MagicMock(jti="test-jti")
                return await call_next(request)

        app.add_middleware(InjectClaims)
        client = TestClient(app)
        resp = client.post(
            "/chat",
            headers={"X-CSRF-Token": "valid-token"},
        )
        assert resp.status_code == 200

    def test_missing_csrf_returns_403(self):
        """Verify missing CSRF token returns 403."""
        app, tokens = self._make_app()
        tokens["test-jti"] = "valid-token"
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectClaims(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.claims = MagicMock(jti="test-jti")
                return await call_next(request)

        app.add_middleware(InjectClaims)
        client = TestClient(app)
        resp = client.post("/chat")
        assert resp.status_code == 403

    def test_invalid_csrf_returns_403(self):
        """Verify wrong CSRF token returns 403."""
        app, tokens = self._make_app()
        tokens["test-jti"] = "valid-token"
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectClaims(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.claims = MagicMock(jti="test-jti")
                return await call_next(request)

        app.add_middleware(InjectClaims)
        client = TestClient(app)
        resp = client.post(
            "/chat",
            headers={"X-CSRF-Token": "wrong-token"},
        )
        assert resp.status_code == 403


class TestBodySizeMiddleware:
    """Tests for body size limit middleware."""

    def _make_app(self, max_size=1_048_576):
        """Create test app with body size middleware."""
        app = Starlette(
            routes=[
                Route("/upload", _ok_handler, methods=["POST"]),
            ],
        )
        app.add_middleware(BodySizeMiddleware, max_size=max_size)
        return app

    def test_normal_request_passes(self):
        """Verify normal-sized request passes."""
        client = TestClient(self._make_app())
        resp = client.post("/upload", content=b"hello")
        assert resp.status_code == 200

    def test_oversized_returns_413(self):
        """Verify oversized request returns 413."""
        client = TestClient(self._make_app(max_size=10))
        resp = client.post(
            "/upload",
            content=b"x" * 20,
            headers={"Content-Length": "20"},
        )
        assert resp.status_code == 413

    def test_exact_limit_passes(self):
        """Verify request at exact limit passes."""
        client = TestClient(self._make_app(max_size=5))
        resp = client.post(
            "/upload",
            content=b"hello",
            headers={"Content-Length": "5"},
        )
        assert resp.status_code == 200

    def test_no_content_length_passes(self):
        """Verify request without Content-Length passes."""
        client = TestClient(self._make_app(max_size=10))
        resp = client.post("/upload")
        assert resp.status_code == 200


class TestRateLimitMiddleware:
    """Tests for rate limiting middleware."""

    def _make_app(self):
        """Create test app with rate limit middleware."""
        limiter = EndpointRateLimiter()
        app = Starlette(
            routes=[
                Route("/stop", _ok_handler, methods=["POST"]),
            ],
        )
        app.add_middleware(RateLimitMiddleware, limiter=limiter)
        return app, limiter

    def test_within_limit_passes(self):
        """Verify request within limit passes."""
        app, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/stop")
        assert resp.status_code == 200

    def test_over_limit_returns_429(self):
        """Verify rate-limited request returns 429."""
        app, limiter = self._make_app()
        for _ in range(10):
            limiter.record("stop")
        client = TestClient(app)
        resp = client.post("/stop")
        assert resp.status_code == 429
        assert "retry-after" in resp.headers

    def test_retry_after_header_present(self):
        """Verify Retry-After header value is positive."""
        app, limiter = self._make_app()
        for _ in range(10):
            limiter.record("stop")
        client = TestClient(app)
        resp = client.post("/stop")
        retry = int(resp.headers["retry-after"])
        assert retry > 0


class TestSecurityHeadersMiddleware:
    """Tests for security headers middleware."""

    def _make_app(self):
        """Create test app with security headers middleware."""
        app = Starlette(
            routes=[Route("/test", _ok_handler)],
        )
        app.add_middleware(SecurityHeadersMiddleware)
        return app

    def test_headers_present(self):
        """Verify all security headers in response."""
        client = TestClient(self._make_app())
        resp = client.get("/test")
        assert resp.status_code == 200
        for key in SECURITY_HEADERS:
            assert key.lower() in {k.lower() for k in resp.headers}

    def test_x_frame_options(self):
        """Verify X-Frame-Options is DENY."""
        client = TestClient(self._make_app())
        resp = client.get("/test")
        assert resp.headers["x-frame-options"] == "DENY"

    def test_content_type_nosniff(self):
        """Verify X-Content-Type-Options is nosniff."""
        client = TestClient(self._make_app())
        resp = client.get("/test")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_referrer_policy(self):
        """Verify Referrer-Policy is set."""
        client = TestClient(self._make_app())
        resp = client.get("/test")
        assert "strict-origin" in resp.headers["referrer-policy"]
