"""Integration tests for the user profile route contract."""

from tests.llm_config_fakes import (
    FakeAdminClient,
    _authed_client_with_csrf,
    _build_test_app,
)


class TestProfileContract:
    """Integration tests for the user profile route contract."""

    def test_profile_returns_wrapped_profile_object(self) -> None:
        """GET /admin/profile should return the handler's profile wrapper."""
        app, token, sm, sc, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(app, token, sm, sc)

        resp = client.get("/admin/profile")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "profile": {
                "display_name": "Ada",
                "timezone": "Europe/Vilnius",
                "language": "lt",
                "custom_instructions": "Be concise",
            }
        }

    def test_profile_save_persists_updated_fields(self) -> None:
        """PUT /admin/profile should persist the raw profile payload."""
        app, token, sm, sc, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        save_resp = client.put(
            "/admin/profile",
            json={
                "display_name": "Grace",
                "timezone": "UTC",
                "language": "en",
                "custom_instructions": "Prefer terse answers",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert save_resp.status_code == 200, save_resp.text

        load_resp = client.get("/admin/profile")
        assert load_resp.status_code == 200, load_resp.text
        assert load_resp.json() == {
            "profile": {
                "first_name": "",
                "last_name": "",
                "display_name": "Grace",
                "assistant_name": "",
                "timezone": "UTC",
                "language": "en",
                "custom_instructions": "Prefer terse answers",
            }
        }
