"""Integration tests for the Discord bridge inbound route."""

from unittest.mock import AsyncMock, MagicMock

from tests.llm_config_fakes import (
    FakeAdminClient,
    FakeChatBridgeService,
    _authed_client_with_csrf,
    _build_test_app,
)


class TestFileReplacement:
    """Integration tests for replacement semantics in file uploads."""

    def test_discord_bridge_inbound_route_requires_bearer_token(self) -> None:
        """Discord inbound bridge route should reject requests without a token."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        app.state.chat_bridge_service = FakeChatBridgeService(app.state.admin_client)
        app.state.settings.discord_bridge_token = "bridge-secret-1"
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/bridge/providers/discord/messages",
            json={
                "external_space_id": "guild-1",
                "external_channel_id": "channel-1",
                "external_message_id": "discord-message-1",
                "author_id": "discord-user-1",
                "author_name": "Ops Admin",
                "content": "Check the latest deploy logs.",
            },
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Bearer token required"

    def test_discord_bridge_inbound_route_saves_message_with_valid_token(self) -> None:
        """Discord inbound route should pass a verified message to the service."""
        app, token, session_manager, signing_client, audit_log = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        app.state.settings.discord_bridge_token = "bridge-secret-1"
        app.state.settings.discord_allowed_user_ids = "discord-user-1"
        app.state.settings.discord_bridge_server_id = "guild-1"
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/bridge/providers/discord/messages",
            json={
                "external_space_id": "guild-1",
                "external_channel_id": "channel-1",
                "external_message_id": "discord-message-1",
                "author_id": "discord-user-1",
                "author_name": "Ops Admin",
                "content": "Check the latest deploy logs.",
            },
            headers={"Authorization": "Bearer bridge-secret-1"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "message_id": "bridge-inbound-message-1",
            "synced": True,
            "reason": "",
        }
        app.state.orchestrator_client.send_bridge_inbound_sync.assert_awaited_once()
        request = (
            app.state.orchestrator_client.send_bridge_inbound_sync.await_args.args[0]
        )
        assert request.provider == "discord"
        assert request.external_space_id == "guild-1"
        assert request.external_channel_id == "channel-1"
        assert request.external_message_id == "discord-message-1"
        assert request.author_id == "discord-user-1"
        assert request.author_name == "Ops Admin"
        assert request.content == "Check the latest deploy logs."
        assert app.state.orchestrator_client.publish_conversation_event.await_count == 3
        publish_calls = (
            app.state.orchestrator_client.publish_conversation_event.await_args_list
        )
        inbound_event = publish_calls[0].kwargs
        assistant_event = publish_calls[1].kwargs
        conversation_event = publish_calls[2].kwargs
        assert inbound_event["conversation_id"] == "conversation-1"
        assert inbound_event["event_type"].value == "bridge_inbound_message"
        assert inbound_event["data"]["message"]["id"] == "bridge-inbound-message-1"
        assert inbound_event["data"]["message"]["content"] == (
            "Check the latest deploy logs."
        )
        assert assistant_event["conversation_id"] == "conversation-1"
        assert assistant_event["event_type"].value == "bridge_inbound_message"
        assert assistant_event["data"]["message"]["role"] == "assistant"
        assert assistant_event["data"]["message"]["content"] == "LLM response text."
        assert conversation_event["conversation_id"] == "conversation-1"
        assert conversation_event["event_type"].value == "conversation_updated"
        assert conversation_event["data"]["conversation_id"] == "conversation-1"
        assert conversation_event["data"]["updated_at"]
        assert audit_log[-1]["event_type"] == "chat_bridge_inbound_sync"
        assert audit_log[-1]["tool_name"] == "chat_bridge.discord"

    def test_discord_bridge_inbound_route_returns_unsynced_result(self) -> None:
        """Discord inbound route should surface an unsynced bridge result."""
        app, token, session_manager, signing_client, audit_log = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        app.state.settings.discord_bridge_token = "bridge-secret-1"
        app.state.settings.discord_allowed_user_ids = "discord-user-1"
        app.state.settings.discord_bridge_server_id = "guild-1"
        app.state.orchestrator_client.send_bridge_inbound_sync = AsyncMock(
            return_value=MagicMock(
                conversation_id="",
                message_id="",
                synced=False,
                reason="bridge_not_found",
            )
        )
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/bridge/providers/discord/messages",
            json={
                "external_space_id": "guild-1",
                "external_channel_id": "channel-missing",
                "external_message_id": "discord-message-404",
                "author_id": "discord-user-1",
                "author_name": "Ops Admin",
                "content": "Check the latest deploy logs.",
            },
            headers={"Authorization": "Bearer bridge-secret-1"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "message_id": "",
            "synced": False,
            "reason": "bridge_not_found",
        }
        app.state.orchestrator_client.publish_conversation_event.assert_not_awaited()
        assert audit_log == []

    def test_discord_bridge_inbound_route_returns_500_for_internal_error(self) -> None:
        """Discord inbound route should not hide real internal failures."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        app.state.settings.discord_bridge_token = "bridge-secret-1"
        app.state.settings.discord_allowed_user_ids = "discord-user-1"
        app.state.settings.discord_bridge_server_id = "guild-1"
        app.state.orchestrator_client.send_bridge_inbound_sync = AsyncMock(
            return_value=MagicMock(
                message_id="",
                synced=False,
                reason="internal_error",
            )
        )
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/bridge/providers/discord/messages",
            json={
                "external_space_id": "guild-1",
                "external_channel_id": "channel-1",
                "external_message_id": "discord-message-500",
                "author_id": "discord-user-1",
                "author_name": "Ops Admin",
                "content": "Check the latest deploy logs.",
            },
            headers={"Authorization": "Bearer bridge-secret-1"},
        )

        assert response.status_code == 500, response.text
        assert response.json()["detail"] == "Bridge inbound processing failed"

    def test_discord_bridge_inbound_route_rejects_unapproved_author(self) -> None:
        """Discord inbound route should reject authors outside the allowed list."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        app.state.settings.discord_bridge_token = "bridge-secret-1"
        app.state.settings.discord_allowed_user_ids = "discord-user-1"
        app.state.settings.discord_bridge_server_id = "guild-1"
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/bridge/providers/discord/messages",
            json={
                "external_space_id": "guild-1",
                "external_channel_id": "channel-1",
                "external_message_id": "discord-message-2",
                "author_id": "discord-user-999",
                "author_name": "Intruder",
                "content": "Check the latest deploy logs.",
            },
            headers={"Authorization": "Bearer bridge-secret-1"},
        )

        assert response.status_code == 403, response.text
        assert (
            response.json()["detail"] == "Discord author is not allowed for bridge sync"
        )

    def test_discord_bridge_inbound_route_rejects_wrong_server(self) -> None:
        """Discord inbound route should reject messages for the wrong server."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        app.state.settings.discord_bridge_token = "bridge-secret-1"
        app.state.settings.discord_allowed_user_ids = "discord-user-1"
        app.state.settings.discord_bridge_server_id = "guild-1"
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/bridge/providers/discord/messages",
            json={
                "external_space_id": "guild-999",
                "external_channel_id": "channel-1",
                "external_message_id": "discord-message-3",
                "author_id": "discord-user-1",
                "author_name": "Ops Admin",
                "content": "Check the latest deploy logs.",
            },
            headers={"Authorization": "Bearer bridge-secret-1"},
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "Discord bridge server mismatch"
