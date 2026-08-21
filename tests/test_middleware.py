"""Tests for the ASGI middleware stack.

Covers the state check, JWT auth, CSRF, body size, rate limit,
cache control, and security headers middleware.
"""

from unittest.mock import MagicMock

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.app_state import AppState, AppStateManager
from backend.channels.endpoints import EndpointRateLimiter
from backend.channels.jwt_auth import (
    SessionManager,
    derive_signing_key,
)
from backend.channels.web import SECURITY_HEADERS
from backend.middleware.body_size import BodySizeMiddleware
from backend.middleware.cache_control import CacheControlMiddleware
from backend.middleware.csrf import CsrfMiddleware
from backend.middleware.jwt_auth import JwtAuthMiddleware
from backend.middleware.rate_limit import RateLimitMiddleware
from backend.middleware.security_headers import (
    SecurityHeadersMiddleware,
)
from backend.middleware.state_check import StateCheckMiddleware


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
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:not-configured"

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
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:locked"

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
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:authentication-required"
        assert resp.json()["detail"] == "Authentication required"

    def test_invalid_token_returns_401(self):
        """Verify invalid token returns 401."""
        app, _ = self._make_app()
        client = TestClient(app, cookies={"access_token": "garbage"})
        resp = client.post("/chat")
        assert resp.status_code == 401
        assert resp.json()["type"] == "urn:bimemo:problem:authentication-required"

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
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:csrf-failed"
        assert resp.json()["detail"] == "CSRF validation failed"

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
        assert resp.json()["type"] == "urn:bimemo:problem:csrf-failed"


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
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:payload-too-large"
        assert resp.json()["detail"] == "Request body too large"

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
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:rate-limited"
        assert resp.json()["detail"] == "Rate limit exceeded"

    def test_retry_after_header_present(self):
        """Verify Retry-After header value is positive."""
        app, limiter = self._make_app()
        for _ in range(10):
            limiter.record("stop")
        client = TestClient(app)
        resp = client.post("/stop")
        retry = int(resp.headers["retry-after"])
        assert retry > 0

    def test_stats_after_record(self):
        """Verify limit/remaining/reset stats track the active window."""
        limiter = EndpointRateLimiter()
        limiter.record("stop")
        assert limiter.limit_for("stop") == 10
        assert limiter.remaining("stop") == 9
        assert 0 <= limiter.reset_seconds("stop") <= 60
        assert limiter.remaining("unknown_endpoint") == 60
        assert limiter.reset_seconds("unknown_endpoint") == 0

    def test_allowed_response_carries_stats(self):
        """Verify allowed responses carry X-RateLimit-Limit/Remaining/Reset."""
        app, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/stop")
        assert resp.status_code == 200
        assert resp.headers["x-ratelimit-limit"] == "10"
        assert resp.headers["x-ratelimit-remaining"] == "9"
        assert 0 <= int(resp.headers["x-ratelimit-reset"]) <= 60

    def test_remaining_decreases_per_request(self):
        """Verify X-RateLimit-Remaining decreases with each allowed request."""
        app, _ = self._make_app()
        client = TestClient(app)
        first = client.post("/stop").headers["x-ratelimit-remaining"]
        second = client.post("/stop").headers["x-ratelimit-remaining"]
        assert (first, second) == ("9", "8")

    def test_429_carries_stats(self):
        """Verify 429 carries X-RateLimit stats with Remaining 0."""
        app, limiter = self._make_app()
        for _ in range(10):
            limiter.record("stop")
        client = TestClient(app)
        resp = client.post("/stop")
        assert resp.status_code == 429
        assert resp.headers["x-ratelimit-limit"] == "10"
        assert resp.headers["x-ratelimit-remaining"] == "0"
        assert 0 <= int(resp.headers["x-ratelimit-reset"]) <= 60
        assert int(resp.headers["retry-after"]) > 0


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


# --- Route-level transport header tests (R7.1, R7.2, R3.3) ---


def _json_handler(request):
    """Return one plain JSON response."""
    return JSONResponse({"ok": True})


def _problem_handler(request):
    """Return one problem+json response."""
    return Response(
        content=b'{"type": "urn:bimemo:problem:error"}',
        media_type="application/problem+json",
        status_code=500,
    )


def _asset_handler(request):
    """Return one static-asset-like response."""
    return Response(content=b"console.log(1)", media_type="text/javascript")


def _index_handler(request):
    """Return one index.html-like response."""
    return Response(content=b"<html></html>", media_type="text/html")


def _explicit_cc_handler(request):
    """Return a JSON response that sets its own Cache-Control."""
    return JSONResponse({"ok": True}, headers={"Cache-Control": "public, max-age=60"})


class TestCacheControlMiddleware:
    """Tests for the Cache-Control policy middleware (R7.3)."""

    def _make_app(self):
        """Create test app with the cache-control middleware."""
        app = Starlette(
            routes=[
                Route("/api/thing", _json_handler),
                Route("/problem", _problem_handler),
                Route("/assets/index-a1b2c3d4e5.js", _asset_handler),
                Route("/assets/plain.js", _asset_handler),
                Route("/", _index_handler),
                Route("/index.html", _index_handler),
                Route("/explicit", _explicit_cc_handler),
            ],
        )
        app.add_middleware(CacheControlMiddleware)
        return app

    def test_json_api_no_store(self):
        """Verify JSON API responses get private, no-store."""
        client = TestClient(self._make_app())
        resp = client.get("/api/thing")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "private, no-store"

    def test_problem_json_no_store(self):
        """Verify problem+json responses get private, no-store."""
        client = TestClient(self._make_app())
        resp = client.get("/problem")
        assert resp.status_code == 500
        assert resp.headers["cache-control"] == "private, no-store"

    def test_hashed_asset_immutable(self):
        """Verify content-hashed dist assets get immutable year cache."""
        client = TestClient(self._make_app())
        resp = client.get("/assets/index-a1b2c3d4e5.js")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"

    def test_unhashed_asset_untouched(self):
        """Verify assets without a content hash get no Cache-Control."""
        client = TestClient(self._make_app())
        resp = client.get("/assets/plain.js")
        assert resp.status_code == 200
        assert "cache-control" not in resp.headers

    def test_index_no_cache(self):
        """Verify the served index gets no-cache on / and /index.html."""
        client = TestClient(self._make_app())
        assert client.get("/").headers["cache-control"] == "no-cache"
        assert client.get("/index.html").headers["cache-control"] == "no-cache"

    def test_explicit_header_preserved(self):
        """Verify a route-set Cache-Control is never overwritten (setdefault)."""
        client = TestClient(self._make_app())
        resp = client.get("/explicit")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=60"
