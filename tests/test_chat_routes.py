"""Integration tests for chat route conversation and bridge behavior."""

from unittest.mock import MagicMock

from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    StructuredAssistantContent,
)
from tests.llm_config_fakes import (
    FakeAdminClient,
    FakeChatBridgeService,
    _authed_client_with_csrf,
    _build_test_app,
)


class TestFileReplacement:
    """Integration tests for replacement semantics in file uploads."""

    def test_chat_uses_provided_conversation_id_for_orchestrator(self) -> None:
        """Chat should forward the explicit conversation identifier."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Capture one outgoing chat request with an explicit conversation id."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/chat",
            json={
                "message": "hello",
                "conversation_id": "chat-123",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert len(sent_requests) == 1
        assert sent_requests[0].conversation_id == "chat-123"

    def test_chat_forwards_client_message_id_to_orchestrator(self) -> None:
        """Chat should forward the client-stable message identifier (R6)."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Capture one outgoing chat request with the client message id."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/chat",
            json={
                "message": "hello",
                "conversation_id": "chat-123",
                "run_id": "run-abc",
                "client_message_id": "cm-abc",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert len(sent_requests) == 1
        assert sent_requests[0].run_id == "run-abc"
        assert sent_requests[0].client_message_id == "cm-abc"

    def test_chat_route_creates_persisted_conversation_when_id_is_missing(self) -> None:
        """Chat should create a persisted conversation instead of using session id."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"test-discord-user-1"}),
        )
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = chat_bridge_service
        app.state.settings.discord_persona_user_ids = "test-discord-user-1"
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for pre-created chat tests."""
            del session_id
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/chat",
            json={
                "message": "Create the bridge for this conversation.",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert response.json()["conversation_id"] == "conversation-3"
        assert len(sent_requests) == 1
        assert sent_requests[0].conversation_id == "conversation-3"
        assert chat_bridge_service.created_conversation_ids == []
        assert chat_bridge_service.created_bridge_titles == []
        assert chat_bridge_service.created_delivery_modes == []
        assert chat_bridge_service.created_persona_modes == []
        assert chat_bridge_service.created_persona_user_ids == []

    def test_chat_route_does_not_auto_create_bridge_for_existing_conversation(
        self,
    ) -> None:
        """Existing conversations should not gain a bridge during message send."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"test-discord-user-1"}),
        )
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = chat_bridge_service
        app.state.settings.discord_persona_user_ids = "test-discord-user-1"
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for existing chat bridge tests."""
            del session_id
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/chat",
            json={
                "message": "hello",
                "conversation_id": "conversation-1",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert len(sent_requests) == 1
        assert sent_requests[0].conversation_id == "conversation-1"
        assert chat_bridge_service.created_bridge_titles == []
        assert admin_client._chat_bridge_links == {}

    def test_create_conversation_route_does_not_auto_create_bridge_when_enabled(
        self,
    ) -> None:
        """Conversation creation should not create a bridge automatically."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"test-discord-user-1"}),
        )
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = chat_bridge_service
        app.state.settings.discord_persona_user_ids = "test-discord-user-1"
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/chat/conversations",
            json={
                "title": "Bridge this new chat please",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 201, response.text
        assert response.json() == {"conversation_id": "conversation-3"}
        assert chat_bridge_service.created_conversation_ids == []
        assert chat_bridge_service.created_bridge_titles == []
        assert admin_client._chat_bridge_links == {}

    def test_create_empty_conversation_route_does_not_create_generic_bridge(
        self,
    ) -> None:
        """An empty chat should not create a generic web-chat Discord channel."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"test-discord-user-1"}),
        )
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = chat_bridge_service
        app.state.settings.discord_persona_user_ids = "test-discord-user-1"
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        response = client.post(
            "/chat/conversations",
            json={"title": ""},
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 201, response.text
        assert response.json() == {"conversation_id": "conversation-3"}
        assert chat_bridge_service.created_conversation_ids == []

    def test_chat_route_does_not_create_bridge_for_existing_empty_conversation(
        self,
    ) -> None:
        """First message should not create a bridge for an empty unlinked chat."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"test-discord-user-1"}),
        )
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = chat_bridge_service
        app.state.settings.discord_persona_user_ids = "test-discord-user-1"

        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed response for existing empty chat tests."""
            del session_id
            sent_requests.append(request)
            return IPCChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="hello back"),
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription="",
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        create_response = client.post(
            "/chat/conversations",
            json={"title": ""},
            headers={"X-CSRF-Token": csrf},
        )
        assert create_response.status_code == 201, create_response.text
        conversation_id = create_response.json()["conversation_id"]
        assert conversation_id == "conversation-3"
        assert chat_bridge_service.created_conversation_ids == []

        response = client.post(
            "/chat",
            json={
                "message": "Bridge this empty chat please",
                "conversation_id": conversation_id,
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert len(sent_requests) == 1
        assert sent_requests[0].conversation_id == conversation_id
        assert chat_bridge_service.created_conversation_ids == []
        assert chat_bridge_service.created_bridge_titles == []
        assert admin_client._chat_bridge_links == {}
        assert chat_bridge_service.synced_conversation_ids == [conversation_id]

    def test_chat_route_processes_existing_empty_conversation_without_bridge_setup(
        self,
    ) -> None:
        """Existing empty chats should reach the orchestrator without bridge setup."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin_client = FakeAdminClient()
        chat_bridge_service = FakeChatBridgeService(
            admin_client,
            allowed_persona_user_ids=frozenset({"test-discord-user-1"}),
        )
        app.state.admin_client = admin_client
        app.state.chat_bridge_service = chat_bridge_service
        app.state.settings.discord_persona_user_ids = "test-discord-user-1"

        operation_order: list[str] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed orchestrator response for an existing empty chat."""
            del session_id
            operation_order.append(f"send_chat:{request.conversation_id}")
            return IPCChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="ok"),
                pending_confirmations=[],
                input_filtered=False,
                output_filtered=False,
                retrieved_memories=[],
                retrieved_messages=[],
                transcription="",
                stats=None,
            )

        original_sync_recent = chat_bridge_service.sync_recent_conversation_messages

        async def tracked_sync_recent(
            *,
            conversation_id: str,
            limit: int = 6,
        ) -> list[object]:
            """Record one bridge sync attempt while preserving the original call."""
            operation_order.append(f"sync_bridge:{conversation_id}")
            return await original_sync_recent(
                conversation_id=conversation_id,
                limit=limit,
            )

        chat_bridge_service.sync_recent_conversation_messages = tracked_sync_recent
        app.state.orchestrator_client.send_chat = send_chat
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        create_response = client.post(
            "/chat/conversations",
            json={"title": ""},
            headers={"X-CSRF-Token": csrf},
        )
        assert create_response.status_code == 201, create_response.text
        conversation_id = create_response.json()["conversation_id"]

        response = client.post(
            "/chat",
            json={
                "message": "Please bridge this chat after the answer",
                "conversation_id": conversation_id,
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert operation_order == [
            f"send_chat:{conversation_id}",
            f"sync_bridge:{conversation_id}",
        ]

    def test_chat_returns_usage_stats_from_orchestrator(self) -> None:
        """Orchestrator usage stats reach the HTTP chat response body.

        The default /chat path streams and delivers the final answer over
        SSE, so this asserts the synchronous response builder that the
        non-streaming path and the SSE final event both source their stats
        from — usage must survive the IPC -> HTTP hop unchanged.
        """
        from unittest.mock import MagicMock

        from backend.processes.fastapi_app.routes.chat_helpers import (
            build_http_chat_response,
        )

        stats = {
            "prompt_tokens": 321,
            "completion_tokens": 54,
            "total_tokens": 375,
            "context": {
                "total": 32768,
                "used": 4096,
                "free": 28672,
                "used_percent": 12.5,
            },
            "timing": {
                "prompt_ms": 450.0,
                "generation_ms": 1600.0,
                "total_ms": 2200.0,
            },
            "model": "qwen3.5:35b",
            "iterations": 2,
        }
        ipc_response = MagicMock(
            input_filtered=False,
            output_filtered=False,
            pending_confirmations=[],
            pause_reason=None,
            pending_input=None,
            used_tools=[],
            retrieved_memories=[],
            retrieved_messages=[],
            transcription=None,
            structured_content=None,
            stream_enabled=False,
            outcome_kind=None,
            run_id="run-1",
            finalization_id=None,
            error=None,
            stats=stats,
        )

        response = build_http_chat_response(
            conversation_id="conv-1",
            ipc_response=ipc_response,
        )
        dumped = response.model_dump()["stats"]
        assert dumped["prompt_tokens"] == 321
        assert dumped["total_tokens"] == 375
        assert dumped["iterations"] == 2
        assert dumped["context"]["used_percent"] == 12.5
        assert dumped["timing"]["total_ms"] == 2200.0
        assert dumped["model"] == "qwen3.5:35b"
