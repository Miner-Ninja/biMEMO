"""Integration tests for the RFC 9457 problem+json contract on the active stack."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.testclient import TestClient

from tests.llm_config_fakes import (
    FakeAdminClient,
    _authed_client_with_csrf,
    _build_test_app,
)


class TestProblemContract:
    """RFC 9457 problem+json contract via the production handlers.

    The test app registers the same exception handlers as the production
    app, so these cases exercise the real handler code (parity).
    """

    def test_422_is_validation_problem_with_errors_extension(self) -> None:
        """A request validation error is a validation-error problem."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        resp = client.post(
            "/admin/llm/capabilities",
            json={"service_type": "ollama", "model": 123},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:validation-error"
        assert any(list(entry["loc"]) == ["body", "model"] for entry in body["errors"])

    def test_malformed_message_page_cursor_is_422_validation_problem(self) -> None:
        """A malformed message page cursor is a 422 validation-error problem."""
        app, token, sm, sc, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(app, token, sm, sc)

        resp = client.get(
            "/chat/conversations/conversation-1/messages?cursor=not-a-cursor"
        )

        assert resp.status_code == 422
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:validation-error"
        assert "cursor" in body["detail"].lower()

    def test_unhandled_exception_is_internal_error_problem(self) -> None:
        """An unhandled exception is a 500 internal-error problem."""
        app, token, _, _, _ = _build_test_app()
        client = TestClient(
            app,
            cookies={"access_token": token},
            raise_server_exceptions=False,
        )

        resp = client.get("/test/unhandled-error")

        assert resp.status_code == 500
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:internal-error"
        assert body["status"] == 500
        assert body["detail"] == "Internal Server Error"

    def test_models_returns_502_on_transport_failure(self) -> None:
        """A provider connection failure is a 502 bad-gateway problem."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        def _raise_client(*args: object, **kwargs: object) -> object:
            raise httpx.ConnectError("connection refused")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("httpx.AsyncClient", _raise_client)
            resp = client.post(
                "/admin/llm/models",
                json={
                    "service_type": "openrouter",
                    "api_key": "sk-test-key",
                    "base_url": "https://openrouter.ai",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 502
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:bad-gateway"
        assert "connection refused" in body["detail"]

    @pytest.mark.parametrize("upstream_status", [401, 403])
    def test_models_maps_upstream_4xx_to_400(self, upstream_status: int) -> None:
        """An upstream 4xx is a 400 upstream-error, never the upstream code."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        response = MagicMock(status_code=upstream_status)

        def _raise_for_status() -> None:
            raise httpx.HTTPStatusError(
                f"{upstream_status} error",
                request=MagicMock(),
                response=response,
            )

        response.raise_for_status = _raise_for_status

        async_client = AsyncMock()
        async_client.__aenter__.return_value = async_client
        async_client.__aexit__.return_value = False
        async_client.get.return_value = response

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("httpx.AsyncClient", lambda *args, **kwargs: async_client)
            resp = client.post(
                "/admin/llm/models",
                json={
                    "service_type": "openrouter",
                    "api_key": "sk-bad-key",
                    "base_url": "https://openrouter.ai",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 400
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:upstream-error"
        assert str(upstream_status) in body["detail"]

    def test_capabilities_returns_502_on_provider_failure(self) -> None:
        """A provider failure during capability detection is a 502."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/show":
                return httpx.Response(404, json={"error": "model not found"})
            if request.url.path == "/api/ps":
                return httpx.Response(500, json={"error": "internal error"})
            raise AssertionError(f"Unexpected path: {request.url.path}")

        real_async_client = httpx.AsyncClient

        with pytest.MonkeyPatch.context() as mp:

            def _mock_async_client(
                *args: object, **kwargs: object
            ) -> httpx.AsyncClient:
                return real_async_client(
                    transport=httpx.MockTransport(handler),
                    base_url=kwargs.get("base_url", "http://ollama:11434"),
                    timeout=kwargs.get("timeout", 20.0),
                )

            mp.setattr("httpx.AsyncClient", _mock_async_client)
            resp = client.post(
                "/admin/llm/capabilities",
                json={
                    "service_type": "ollama",
                    "model": "qwen3.5:9b",
                    "base_url": "https://ollama.example.test",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 502
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:bad-gateway"
        assert "/api/ps" in body["detail"]

    def test_capabilities_returns_404_for_unknown_model(self) -> None:
        """A model the host does not know is a 404 not-found problem."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/show":
                return httpx.Response(404, json={"error": "model not found"})
            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": []})
            if request.url.path == "/api/generate":
                return httpx.Response(
                    404,
                    json={
                        "error": (
                            "model 'ghost' not found, try 'ollama pull ghost' first"
                        )
                    },
                )
            raise AssertionError(f"Unexpected path: {request.url.path}")

        real_async_client = httpx.AsyncClient

        with pytest.MonkeyPatch.context() as mp:

            def _mock_async_client(
                *args: object, **kwargs: object
            ) -> httpx.AsyncClient:
                return real_async_client(
                    transport=httpx.MockTransport(handler),
                    base_url=kwargs.get("base_url", "http://ollama:11434"),
                    timeout=kwargs.get("timeout", 20.0),
                )

            mp.setattr("httpx.AsyncClient", _mock_async_client)
            resp = client.post(
                "/admin/llm/capabilities",
                json={
                    "service_type": "ollama",
                    "model": "ghost",
                    "base_url": "https://ollama.example.test",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:not-found"
        assert body["detail"] == (
            "model 'ghost' not found, try 'ollama pull ghost' first"
        )

    def test_unknown_path_is_not_found_problem(self) -> None:
        """An unknown path is a 404 not-found problem with 'Not Found' detail."""
        app, token, _, _, _ = _build_test_app()
        client = TestClient(
            app,
            cookies={"access_token": token},
            raise_server_exceptions=False,
        )
        resp = client.get("/definitely-not-a-route")
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:not-found"
        assert body["detail"] == "Not Found"
