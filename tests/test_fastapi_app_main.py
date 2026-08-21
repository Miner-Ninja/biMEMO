"""Behaviour contract tests for the FastAPI application factory (F27).

Covers what ``create_app`` builds before the IPC lifespan runs: the app
identity (no interactive docs), the exact middleware stack order, the
pre-lifespan state defaults, the registered route table, the SPA 404
fallback that serves the frontend index for HTML navigations, and the
401 problem document for unauthenticated API requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from backend.processes.fastapi_app.main import create_app


if TYPE_CHECKING:
    from pathlib import Path


SOCKET_ARGS = {
    "orchestrator_socket": "/tmp/orch.sock",
    "orchestrator_secret": b"secret",
    "db_proxy_socket_b": "/tmp/db.sock",
    "db_proxy_secret_b": b"secret",
}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """An app whose frontend dist is a controlled temporary directory."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>index</html>")
    monkeypatch.setattr(
        "backend.processes.fastapi_app.main.FRONTEND_DIST",
        dist,
    )
    return create_app(**SOCKET_ARGS)


class TestAppIdentity:
    """The app is the biMEMO API without interactive documentation."""

    def test_title_and_docs_disabled(self, app):
        """The OpenAPI title is set; the interactive docs are disabled."""
        assert app.title == "biMEMO"
        assert app.docs_url is None
        assert app.redoc_url is None
        document_paths = set(app.openapi()["paths"])
        assert "/docs" not in document_paths
        assert "/redoc" not in document_paths

    def test_openapi_json_route_is_registered(self, app):
        """The OpenAPI document endpoint is part of the route table."""
        assert "/openapi.json" in {getattr(r, "path", "") for r in app.routes}


class TestMiddlewareStack:
    """The security middleware is present, in the documented order."""

    def test_middleware_order(self, app):
        """Cache control is outermost; rate limiting is innermost.

        ``add_middleware`` inserts at the front of ``user_middleware``,
        so the list is ordered outermost-first: the last middleware
        added in ``create_app`` appears first here.
        """
        names = [layer.cls.__name__ for layer in app.user_middleware]
        assert names == [
            "CacheControlMiddleware",
            "SecurityHeadersMiddleware",
            "BodySizeMiddleware",
            "JwtAuthMiddleware",
            "CsrfMiddleware",
            "RateLimitMiddleware",
        ]


class TestPreLifespanState:
    """The state placeholders set before the IPC lifespan runs."""

    def test_managers_and_placeholders(self, app):
        """Managers are real instances; late-bound clients are None."""
        assert app.state.session_manager is not None
        assert app.state.realtime_manager is not None
        assert app.state.reauth_manager is not None
        assert app.state.rate_limiter is not None
        assert app.state.signing_client is None
        assert app.state.chat_bridge_service is None
        assert app.state.audit_fn is None


class TestRouteTable:
    """The API surface registered by the factory."""

    def test_core_resource_paths_are_present(self, app):
        """The main resource paths all appear in the OpenAPI document."""
        paths = set(app.openapi()["paths"])
        for expected in (
            "/health",
            "/auth/login",
            "/admin/scheduled-tasks",
            "/admin/scheduled-tasks/{task_id}",
            "/admin/scheduled-tasks/{task_id}/runs",
            "/admin/tools",
            "/admin/trace",
            "/admin/trace/{run_id}",
            "/workspace/tree",
            "/workspace/dirs",
            "/tts",
            "/tts/status",
        ):
            assert expected in paths, expected

    def test_openapi_document_covers_the_whole_api(self, app):
        """The document describes the full endpoint set."""
        document = app.openapi()
        assert document["info"]["title"] == "biMEMO"
        assert len(document["paths"]) > 50


class TestRequestGate:
    """The auth gate as seen by the network before any route runs."""

    async def test_unauthenticated_html_navigation_passes_through(self, app):
        """A tokenless HTML request passes the auth gate.

        The SPA fallback can serve the index so the React app can
        redirect to login.
        """
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/definitely/not/a/route",
                headers={"Accept": "text/html"},
            )
        assert response.status_code == 200
        assert "<html>index</html>" in response.text
        assert response.headers["cache-control"] == "no-cache"

    async def test_unauthenticated_api_request_is_401_problem(self, app):
        """A tokenless JSON request is a 401 problem document.

        The RFC 9457 problem response is returned, not the index.
        """
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/definitely/not/a/route",
                headers={"Accept": "application/json"},
            )
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"] == "urn:bimemo:problem:authentication-required"
        assert "index" not in response.text
