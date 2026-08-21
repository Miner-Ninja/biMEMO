"""Integration tests for auth refresh and retry-after header propagation."""

from starlette.testclient import TestClient

from tests.llm_config_fakes import (
    _authed_client_with_csrf,
    _awaitable_result,
    _build_test_app,
)


class TestRefreshRouteActiveStack:
    """Integration tests for refresh behavior in the active FastAPI stack."""

    def test_refresh_succeeds_with_refresh_cookie_only(self) -> None:
        """Expired or missing access token should not be required for refresh."""
        app, _, _, signing_client, _ = _build_test_app()
        client = TestClient(app)

        refresh_token = app.state.session_manager.async_issue_refresh_token(
            signing_client,
        )
        token_value = _awaitable_result(refresh_token)
        client.cookies.set("refresh_token", token_value)

        resp = client.post("/refresh")
        assert resp.status_code == 200, resp.text
        assert "access_token" in resp.cookies
        assert "refresh_token" in resp.cookies

    def test_refresh_rejects_access_token_in_refresh_cookie(self) -> None:
        """Refresh endpoint must reject cookies that carry an access token."""
        app, access_token, _, _, _ = _build_test_app()
        client = TestClient(app)
        client.cookies.set("refresh_token", access_token)

        resp = client.post("/refresh")
        assert resp.status_code == 401


class TestAuthRateLimitHeaders:
    """Integration tests for propagated retry-after headers."""

    def test_login_uses_retry_after_from_signing_proxy(self) -> None:
        """POST /auth/login should preserve dynamic retry-after from signing proxy."""
        app, _, _, _, _ = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/auth/login",
            json={"password": "rate-limited-password"},
        )

        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "120"

    def test_reauth_uses_retry_after_from_signing_proxy(self) -> None:
        """POST /admin/reauth should preserve dynamic retry-after from signing proxy."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        resp = client.post(
            "/admin/reauth",
            json={"password": "rate-limited-password"},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "120"
