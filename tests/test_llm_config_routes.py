"""Integration tests for LLM config CRUD, integrations, and chat bridge link routes."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.testclient import TestClient

from tests.llm_config_fakes import (
    FakeAdminClient,
    FakeChatBridgeService,
    FakeCredentialClient,
    _authed_client,
    _authed_client_with_csrf,
    _awaitable_result,
    _build_test_app,
)


class TestLLMConfigCRUD:
    """Integration tests for the LLM config save/read/update/delete flow."""

    def _setup(self) -> tuple[TestClient, str, FakeCredentialClient]:
        """Build app with fake credential client and return authed client."""
        app, token, sm, sc, _ = _build_test_app()
        cred = FakeCredentialClient()
        app.state.credential_client = cred
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)
        return client, csrf, cred

    def test_create_and_list_config(self) -> None:
        """POST /admin/llm/configs -> GET /admin/llm/configs returns the config."""
        client, csrf, _credential_client = self._setup()
        headers = {"X-CSRF-Token": csrf}

        # Create a config
        create_resp = client.post(
            "/admin/llm/configs",
            json={
                "service_type": "openrouter",
                "api_key": "sk-test-key-12345",
                "model": "openai/gpt-4o",
                "base_url": "https://openrouter.ai",
            },
            headers=headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        assert (
            create_resp.headers["location"]
            == f"/admin/llm/configs/{create_resp.json()['id']}"
        )
        config_id = create_resp.json()["id"]
        assert config_id is not None

        # List configs
        list_resp = client.get("/admin/llm/configs")
        assert list_resp.status_code == 200
        configs = list_resp.json()["configs"]
        assert len(configs) == 1
        assert configs[0]["id"] == config_id
        assert configs[0]["service_type"] == "openrouter"
        assert configs[0]["model"] == "openai/gpt-4o"

    def test_create_multiple_configs(self) -> None:
        """Create two configs and verify both appear in list."""
        client, csrf, _credential_client = self._setup()
        headers = {"X-CSRF-Token": csrf}

        client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "gpt-4o"},
            headers=headers,
        )
        client.post(
            "/admin/llm/configs",
            json={
                "service_type": "ollama",
                "model": "llama3",
                "base_url": "http://ollama:11434",
                "dialog_recorder_base_url": "http://ollama-memory:11435",
            },
            headers=headers,
        )

        list_resp = client.get("/admin/llm/configs")
        configs = list_resp.json()["configs"]
        assert len(configs) == 2
        types = {c["service_type"] for c in configs}
        assert types == {"openrouter", "ollama"}
        ollama_config = next(c for c in configs if c["service_type"] == "ollama")
        assert ollama_config["dialog_recorder_base_url"] == "http://ollama-memory:11435"

    def test_create_config_invalid_service_type(self) -> None:
        """POST with invalid service_type returns 422."""
        client, csrf, _ = self._setup()
        resp = client.post(
            "/admin/llm/configs",
            json={"service_type": "invalid-provider", "model": "x"},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422

    def test_update_config(self) -> None:
        """Update a config's model, reload runtime, and verify persistence."""
        client, csrf, cred = self._setup()
        headers = {"X-CSRF-Token": csrf}

        create_resp = client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "old-model"},
            headers=headers,
        )
        config_id = create_resp.json()["id"]

        update_resp = client.patch(
            f"/admin/llm/configs/{config_id}",
            json={"model": "new-model"},
            headers=headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["detail"] == "Configuration updated and applied."
        assert update_resp.json()["reload"]["success"] is True
        assert client.app.state.orchestrator_client.send_reload_llm.await_count == 1

        # Verify via in-memory store
        assert cred._configs[config_id]["credentials"]["model"] == "new-model"

    def test_update_nonexistent_returns_404(self) -> None:
        """PATCH on nonexistent config returns 404."""
        client, csrf, _ = self._setup()
        resp = client.patch(
            "/admin/llm/configs/nonexistent-id",
            json={"model": "x"},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 404
        # R2: a 404 raised by a known route keeps its own detail text.
        assert resp.headers["content-type"] == "application/problem+json"
        body = resp.json()
        assert body["type"] == "urn:bimemo:problem:not-found"
        assert body["detail"] == "Configuration not found"

    def test_delete_config(self) -> None:
        """Delete a config, reload runtime, and verify it disappears from list."""
        client, csrf, _credential_client = self._setup()
        headers = {"X-CSRF-Token": csrf}

        create_resp = client.post(
            "/admin/llm/configs",
            json={
                "service_type": "custom",
                "model": "my-model",
                "base_url": "http://local:8080",
            },
            headers=headers,
        )
        config_id = create_resp.json()["id"]

        delete_resp = client.delete(
            f"/admin/llm/configs/{config_id}",
            headers=headers,
        )
        assert delete_resp.status_code == 204
        assert delete_resp.content == b""
        assert client.app.state.orchestrator_client.send_reload_llm.await_count == 1

        list_resp = client.get("/admin/llm/configs")
        assert len(list_resp.json()["configs"]) == 0

    def test_delete_nonexistent_returns_404(self) -> None:
        """DELETE on nonexistent config returns 404."""
        client, csrf, _ = self._setup()
        resp = client.delete(
            "/admin/llm/configs/nonexistent-id",
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 404

    def test_activate_config(self) -> None:
        """Activate a config, reload runtime, and verify it becomes active."""
        client, csrf, _credential_client = self._setup()
        headers = {"X-CSRF-Token": csrf}

        create_resp = client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "gpt-4o"},
            headers=headers,
        )
        config_id = create_resp.json()["id"]

        activate_resp = client.post(
            f"/admin/llm/configs/{config_id}/activate",
            headers=headers,
        )
        assert activate_resp.status_code == 200
        assert activate_resp.json()["detail"] == "Configuration applied."
        assert activate_resp.json()["reload"]["success"] is True
        assert client.app.state.orchestrator_client.send_reload_llm.await_count == 1

        list_resp = client.get("/admin/llm/configs")
        configs = list_resp.json()["configs"]
        assert configs[0]["active"] is True

    def test_activate_deactivates_others(self) -> None:
        """Activating one config deactivates all others."""
        client, csrf, cred = self._setup()
        headers = {"X-CSRF-Token": csrf}

        resp1 = client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "gpt-4o"},
            headers=headers,
        )
        id1 = resp1.json()["id"]

        resp2 = client.post(
            "/admin/llm/configs",
            json={"service_type": "ollama", "model": "llama3"},
            headers=headers,
        )
        id2 = resp2.json()["id"]

        # Activate first
        client.post(f"/admin/llm/configs/{id1}/activate", headers=headers)
        assert cred._configs[id1]["active"] is True
        assert cred._configs[id2]["active"] is False

        # Activate second
        client.post(f"/admin/llm/configs/{id2}/activate", headers=headers)
        assert cred._configs[id1]["active"] is False
        assert cred._configs[id2]["active"] is True

    def test_full_lifecycle(self) -> None:
        """Create -> activate -> update -> read -> delete."""
        client, csrf, _credential_client = self._setup()
        headers = {"X-CSRF-Token": csrf}

        # Create
        create_resp = client.post(
            "/admin/llm/configs",
            json={
                "service_type": "openrouter",
                "api_key": "sk-secret",
                "model": "gpt-4o",
                "base_url": "https://openrouter.ai",
            },
            headers=headers,
        )
        assert create_resp.status_code == 201
        config_id = create_resp.json()["id"]

        # Activate
        client.post(f"/admin/llm/configs/{config_id}/activate", headers=headers)

        # Update model
        client.patch(
            f"/admin/llm/configs/{config_id}",
            json={"model": "gpt-4o-mini"},
            headers=headers,
        )

        # Read back via list
        list_resp = client.get("/admin/llm/configs")
        configs = list_resp.json()["configs"]
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg["active"] is True
        assert cfg["model"] == "gpt-4o-mini"
        assert cfg["service_type"] == "openrouter"

        # Delete
        client.delete(f"/admin/llm/configs/{config_id}", headers=headers)
        list_resp = client.get("/admin/llm/configs")
        assert len(list_resp.json()["configs"]) == 0

    def test_activate_config_without_orchestrator_connection_returns_restart_hint(
        self,
    ) -> None:
        """Activation should still succeed when runtime reload is unavailable."""
        app, token, sm, sc, _ = _build_test_app()
        app.state.credential_client = FakeCredentialClient()
        app.state.orchestrator_client.is_connected = False
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)
        headers = {"X-CSRF-Token": csrf}

        create_resp = client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "gpt-4o"},
            headers=headers,
        )
        config_id = create_resp.json()["id"]

        activate_resp = client.post(
            f"/admin/llm/configs/{config_id}/activate",
            headers=headers,
        )

        assert activate_resp.status_code == 200
        assert activate_resp.json() == {
            "detail": "Configuration activated. Restart to apply.",
            "reload": {"success": False, "error": "orchestrator not connected"},
        }

    def test_config_without_csrf_rejected(self) -> None:
        """POST /admin/llm/configs without CSRF token returns 403."""
        app, token, _session_manager, _signing_client, _ = _build_test_app()
        app.state.credential_client = FakeCredentialClient()
        client = _authed_client(app, token)

        # Obtain CSRF but do NOT send it
        client.get("/csrf-token")

        resp = client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "gpt-4o"},
            # No X-CSRF-Token header
        )
        assert resp.status_code == 403

    def test_config_without_auth_rejected(self) -> None:
        """POST /admin/llm/configs without auth returns 401."""
        app, _, _, _, _ = _build_test_app()
        app.state.credential_client = FakeCredentialClient()
        client = TestClient(app)

        resp = client.post(
            "/admin/llm/configs",
            json={"service_type": "openrouter", "model": "gpt-4o"},
        )
        assert resp.status_code == 401

    def test_delete_integration_requires_reauth_header(self) -> None:
        """Integration delete must require the reauth header token."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        credential_client = FakeCredentialClient()
        app.state.credential_client = credential_client
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        awaitable = credential_client.create_integration(
            "demo",
            "telegram",
            {"bot_token": "abc"},
        )
        _awaitable_result(awaitable)
        reauth_token = app.state.reauth_manager.issue_token()

        missing_header = client.delete("/admin/integrations/demo")
        assert missing_header.status_code == 403

        wrong_query = client.delete(
            f"/admin/integrations/demo?reauth_token={reauth_token}",
        )
        assert wrong_query.status_code == 403

        ok_resp = client.delete(
            "/admin/integrations/demo",
            headers={"X-Reauth-Token": reauth_token, "X-CSRF-Token": csrf},
        )
        assert ok_resp.status_code == 204
        assert ok_resp.content == b""

    def test_list_chat_bridge_links_returns_admin_payload(self) -> None:
        """GET /admin/chat-bridges should return stored bridge links."""
        app, token, _, _, _ = _build_test_app()
        admin_client = FakeAdminClient()
        admin_client._chat_bridge_links["bridge-1"] = {
            "id": "bridge-1",
            "conversation_id": "conversation-1",
            "provider": "discord",
            "external_space_id": "guild-1",
            "external_channel_id": "channel-1",
            "created_by": "admin-user",
            "sync_direction": "bidirectional",
            "status": "active",
            "delivery_mode": "bot_only",
            "persona_mode": "neutral",
            "persona_user_id": "",
            "created_at": "2026-03-25T10:00:00.000",
        }
        app.state.admin_client = admin_client
        client = _authed_client(app, token)

        response = client.get("/admin/chat-bridges")

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": "bridge-1",
                "conversation_id": "conversation-1",
                "provider": "discord",
                "external_space_id": "guild-1",
                "external_channel_id": "channel-1",
                "created_by": "admin-user",
                "sync_direction": "bidirectional",
                "status": "active",
                "delivery_mode": "bot_only",
                "persona_mode": "neutral",
                "persona_user_id": "",
                "created_at": "2026-03-25T10:00:00.000",
            }
        ]

    def test_list_chat_bridge_providers_returns_configured_service_providers(
        self,
    ) -> None:
        """GET /admin/chat-bridge-providers should list selectable providers."""
        app, token, _, _, _ = _build_test_app()
        admin_client = FakeAdminClient()
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"discord-user-1"}),
        )
        client = _authed_client(app, token)

        response = client.get("/admin/chat-bridge-providers")

        assert response.status_code == 200
        assert response.json() == [
            {
                "provider": "discord",
                "default_persona_user_id": "discord-user-1",
            }
        ]

    def test_create_chat_bridge_link_requires_reauth_and_stores_link(self) -> None:
        """POST /admin/chat-bridges should require reauth and store one link."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = FakeChatBridgeService(admin_client)
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )
        reauth_token = app.state.reauth_manager.issue_token()

        response = client.post(
            "/admin/chat-bridges",
            json={
                "conversation_id": "conversation-1",
                "provider": "discord",
                "sync_direction": "bidirectional",
                "title": "Ops bridge room",
            },
            headers={
                "X-CSRF-Token": csrf,
                "X-Reauth-Token": reauth_token,
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["conversation_id"] == "conversation-1"
        assert payload["provider"] == "discord"
        assert payload["status"] == "active"
        assert payload["external_channel_id"] == "channel-1"
        assert payload["delivery_mode"] == "bot_only"
        assert payload["persona_mode"] == "neutral"
        assert payload["persona_user_id"] == ""
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_call = (
            app.state.orchestrator_client.publish_conversation_event.await_args
        )
        assert publish_call.kwargs["conversation_id"] == "conversation-1"
        assert publish_call.kwargs["event_type"].value == "bridge_link_updated"
        assert publish_call.kwargs["data"]["bridge_link"]["status"] == "active"

    def test_disable_chat_bridge_link_requires_reauth_and_updates_status(self) -> None:
        """POST /admin/chat-bridges/{id}/disable should disable one link."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        admin_client._chat_bridge_links["bridge-1"] = {
            "id": "bridge-1",
            "conversation_id": "conversation-1",
            "provider": "discord",
            "external_space_id": "guild-1",
            "external_channel_id": "channel-1",
            "created_by": "admin-user",
            "sync_direction": "bidirectional",
            "status": "active",
            "delivery_mode": "bot_only",
            "persona_mode": "neutral",
            "persona_user_id": "",
            "created_at": "2026-03-25T10:00:00.000",
        }
        app.state.admin_client = admin_client
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )
        reauth_token = app.state.reauth_manager.issue_token()

        response = client.post(
            "/admin/chat-bridges/bridge-1/disable",
            headers={
                "X-CSRF-Token": csrf,
                "X-Reauth-Token": reauth_token,
            },
        )

        assert response.status_code == 200
        assert response.json()["updated"] is True
        assert response.json()["chat_bridge_link"]["status"] == "disabled"
        assert admin_client._chat_bridge_links["bridge-1"]["status"] == "disabled"
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_call = (
            app.state.orchestrator_client.publish_conversation_event.await_args
        )
        assert publish_call.kwargs["conversation_id"] == "conversation-1"
        assert publish_call.kwargs["event_type"].value == "bridge_link_updated"
        assert publish_call.kwargs["data"]["bridge_link"]["status"] == "disabled"

    def test_enable_chat_bridge_link_requires_reauth_and_updates_status(self) -> None:
        """POST /admin/chat-bridges/{id}/enable should enable one link."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        admin_client._chat_bridge_links["bridge-1"] = {
            "id": "bridge-1",
            "conversation_id": "conversation-1",
            "provider": "discord",
            "external_space_id": "guild-1",
            "external_channel_id": "channel-1",
            "created_by": "admin-user",
            "sync_direction": "bidirectional",
            "status": "disabled",
            "delivery_mode": "bot_only",
            "persona_mode": "neutral",
            "persona_user_id": "",
            "created_at": "2026-03-25T10:00:00.000",
        }
        app.state.admin_client = admin_client
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )
        reauth_token = app.state.reauth_manager.issue_token()

        response = client.post(
            "/admin/chat-bridges/bridge-1/enable",
            headers={
                "X-CSRF-Token": csrf,
                "X-Reauth-Token": reauth_token,
            },
        )

        assert response.status_code == 200
        assert response.json()["updated"] is True
        assert response.json()["chat_bridge_link"]["status"] == "active"
        assert admin_client._chat_bridge_links["bridge-1"]["status"] == "active"
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_call = (
            app.state.orchestrator_client.publish_conversation_event.await_args
        )
        assert publish_call.kwargs["conversation_id"] == "conversation-1"
        assert publish_call.kwargs["event_type"].value == "bridge_link_updated"
        assert publish_call.kwargs["data"]["bridge_link"]["status"] == "active"

    def test_update_chat_bridge_link_settings_requires_reauth_and_saves_mode(
        self,
    ) -> None:
        """PUT /admin/chat-bridges/{id}/settings should store bridge send mode."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        admin_client._chat_bridge_links["bridge-1"] = {
            "id": "bridge-1",
            "conversation_id": "conversation-1",
            "provider": "discord",
            "external_space_id": "guild-1",
            "external_channel_id": "channel-1",
            "created_by": "admin-user",
            "sync_direction": "bidirectional",
            "status": "active",
            "delivery_mode": "bot_only",
            "persona_mode": "neutral",
            "persona_user_id": "",
            "created_at": "2026-03-25T10:00:00.000",
        }
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"discord-user-1"}),
        )
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )
        reauth_token = app.state.reauth_manager.issue_token()

        response = client.put(
            "/admin/chat-bridges/bridge-1/settings",
            json={
                "delivery_mode": "persona_user_and_bot_assistant",
                "persona_mode": "mapped_user",
                "persona_user_id": "discord-user-1",
            },
            headers={
                "X-CSRF-Token": csrf,
                "X-Reauth-Token": reauth_token,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["delivery_mode"] == "persona_user_and_bot_assistant"
        assert payload["persona_mode"] == "mapped_user"
        assert payload["persona_user_id"] == "discord-user-1"
        assert (
            admin_client._chat_bridge_links["bridge-1"]["delivery_mode"]
            == "persona_user_and_bot_assistant"
        )
        assert admin_client._chat_bridge_links["bridge-1"]["persona_mode"] == (
            "mapped_user"
        )
        assert (
            admin_client._chat_bridge_links["bridge-1"]["persona_user_id"]
            == "discord-user-1"
        )
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_call = (
            app.state.orchestrator_client.publish_conversation_event.await_args
        )
        assert publish_call.kwargs["conversation_id"] == "conversation-1"
        assert publish_call.kwargs["event_type"].value == "bridge_link_updated"

    def test_list_chat_bridge_message_links_returns_admin_payload(self) -> None:
        """GET bridge message mappings should return stored direction metadata."""
        app, token, _, _, _ = _build_test_app()
        admin_client = FakeAdminClient()
        admin_client._chat_bridge_message_links["bridge-message-1"] = {
            "id": "bridge-message-1",
            "bridge_link_id": "bridge-1",
            "message_id": "web-message-1",
            "external_message_id": "discord-message-1",
            "direction": "outbound",
            "finalization_id": "final-1",
            "created_at": "2026-03-25T10:02:00Z",
        }
        app.state.admin_client = admin_client
        client = _authed_client(app, token)

        response = client.get("/admin/chat-bridges/bridge-1/messages")

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": "bridge-message-1",
                "bridge_link_id": "bridge-1",
                "message_id": "web-message-1",
                "external_message_id": "discord-message-1",
                "direction": "outbound",
                "created_at": "2026-03-25T10:02:00Z",
            }
        ]

    def test_reauth_uses_password_check_without_issuing_tokens(self) -> None:
        """Reauth must validate passwords without issuing new JWTs."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        signing_client.verify_password = AsyncMock(
            side_effect=AssertionError("verify_password should not be used"),
        )
        signing_client.check_password = AsyncMock(return_value={"ok": True})

        resp = client.post(
            "/admin/reauth",
            json={"password": "correct-password"},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200, resp.text
        assert "reauth_token" in resp.json()
        signing_client.check_password.assert_awaited_once_with("correct-password")

    def test_auth_status_returns_503_without_signing_proxy(self) -> None:
        """GET /auth/status should fail cleanly when signing proxy is unavailable."""
        app, token, _, _, _ = _build_test_app()
        app.state.signing_client = None
        client = _authed_client(app, token)

        resp = client.get("/auth/status")
        assert resp.status_code == 503

    def test_llm_configs_returns_503_without_credential_proxy(self) -> None:
        """LLM configs should fail cleanly when the credential proxy is missing."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.credential_client = None
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/llm/configs")
        assert resp.status_code == 503

    def test_model_list_uses_post_body(self) -> None:
        """POST /admin/llm/models accepts provider credentials in request body."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        async_client = AsyncMock()
        async_client.__aenter__.return_value = async_client
        async_client.__aexit__.return_value = False
        async_client.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "openai/gpt-4o-mini"}]},
            raise_for_status=lambda: None,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("httpx.AsyncClient", lambda *args, **kwargs: async_client)
            resp = client.post(
                "/admin/llm/models",
                json={
                    "service_type": "openrouter",
                    "api_key": "sk-test-key",
                    "base_url": "https://openrouter.ai",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"models": ["openai/gpt-4o-mini"]}

    def test_custom_model_list_omits_empty_auth_header(self) -> None:
        """Custom model listing must not send Authorization when API key is empty."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        async_client = AsyncMock()
        async_client.__aenter__.return_value = async_client
        async_client.__aexit__.return_value = False
        async_client.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "qwen3.5:4b"}]},
            raise_for_status=lambda: None,
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("httpx.AsyncClient", lambda *args, **kwargs: async_client)
            resp = client.post(
                "/admin/llm/models",
                json={
                    "service_type": "custom",
                    "api_key": "",
                    "base_url": "http://vllm-primary:8000",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"models": ["qwen3.5:4b"]}
        assert async_client.get.await_args.kwargs["headers"] == {}

    def test_custom_probe_omits_empty_auth_header(self) -> None:
        """Custom provider probe must not send Authorization when API key is empty."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        async_client = AsyncMock()
        async_client.__aenter__.return_value = async_client
        async_client.__aexit__.return_value = False
        async_client.post.return_value = MagicMock(
            status_code=200,
            text="ok",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("httpx.AsyncClient", lambda *args, **kwargs: async_client)
            resp = client.post(
                "/admin/llm/test",
                json={
                    "service_type": "custom",
                    "api_key": "",
                    "model": "qwen3.5:4b",
                    "base_url": "http://vllm-primary:8000",
                },
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "provider": "custom"}
        assert async_client.post.await_args.kwargs["headers"] == {
            "Content-Type": "application/json",
        }

    def test_ollama_capabilities_route_returns_runtime_context_and_kv_estimate(
        self,
    ) -> None:
        """POST /admin/llm/capabilities must use runtime context from /api/ps."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/show":
                assert request.method == "POST"
                return httpx.Response(
                    200,
                    json={
                        "model_info": {
                            "general.architecture": "qwen35",
                            "qwen35.block_count": 32,
                            "qwen35.attention.head_count": 16,
                            "qwen35.attention.key_length": 256,
                            "qwen35.attention.value_length": 256,
                            "qwen35.context_length": 262144,
                        }
                    },
                )
            if request.url.path == "/api/ps":
                assert request.method == "GET"
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "qwen3.5:9b",
                                "context_length": 20480,
                                "size": 9319211648,
                                "size_vram": 9319211648,
                            }
                        ]
                    },
                )
            raise AssertionError(f"Unexpected path: {request.url.path}")

        real_async_client = httpx.AsyncClient

        with pytest.MonkeyPatch.context() as mp:

            def _mock_async_client(*args, **kwargs) -> httpx.AsyncClient:
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

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "model": "qwen3.5:9b",
            "source": "ollama:/api/ps",
            "runtime_context_tokens": 20480,
            "model_max_context_tokens": 262144,
            "model_size_bytes": 9319211648,
            "size_vram_bytes": 9319211648,
            "kv_bytes_per_token": 524288,
            "estimated_runtime_kv_bytes": 10737418240,
            "estimated_runtime_kv_gib": 10.0,
        }
