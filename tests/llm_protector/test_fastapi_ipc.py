"""Tests for FastAPI process IPC components.

Covers OrchestratorClient multiplexing, AdminDBClient operations,
chat flow via IPC, SSE streaming, admin routes via Socket B,
and behavior on lost connections. Uses mock IPC to avoid actual sockets.
"""

import asyncio

import pytest

from backend.ipc.ipc_channel import IPCChannelError
from backend.ipc.models import (
    ChatRequest as IPCChatRequest,
)
from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    ConfirmRequest,
    ConfirmResponse,
    DBOperation,
    DBResult,
    ReloadLLMResponse,
    StreamEvent,
    StreamEventType,
    StructuredAssistantContent,
)
from backend.ipc.models import (
    ConfirmRequest as IPCConfirmRequest,
)
from backend.ipc.models import (
    ConfirmResponse as IPCConfirmResponse,
)
from backend.ipc.models import (
    StopRequest as IPCStopRequest,
)
from backend.ipc.models import (
    StopResponse as IPCStopResponse,
)
from backend.processes.fastapi_app import (
    AdminDBClient,
    CredentialClient,
    OrchestratorClient,
    SigningClient,
)
from backend.processes.orchestrator import OrchestratorIPCServer


class FakeOrchestratorIPC:
    """Mock IPC client that simulates Orchestrator responses."""

    def __init__(self) -> None:
        """Initialize empty send and receive queues."""
        self._sent: list[object] = []
        self._receive_queue: asyncio.Queue[object] = asyncio.Queue()
        self._closed = False

    def queue_receive(self, message: object) -> None:
        """Queue a message to be returned by ``receive``."""
        self._receive_queue.put_nowait(message)

    async def send(self, message: object) -> None:
        """Record a sent IPC message."""
        self._sent.append(message)

    async def receive(self) -> object:
        """Return the next queued message."""
        if self._closed:
            raise IPCChannelError("Connection closed")
        return await self._receive_queue.get()

    async def send_and_receive(self, message: object) -> object:
        """Send a message and return the next queued response."""
        await self.send(message)
        return await self.receive()

    async def close(self) -> None:
        """Mark the fake connection as closed."""
        self._closed = True

    @property
    def is_connected(self) -> bool:
        """Return whether the fake channel is still open."""
        return not self._closed

    @property
    def sent_messages(self) -> list[object]:
        """Return all sent IPC messages."""
        return self._sent


class FakeDBProxyIPC:
    """Mock IPC client that returns preset ``DBResult`` responses."""

    def __init__(self) -> None:
        """Initialize empty sent-message and response queues."""
        self._responses: list[DBResult] = []
        self._sent: list[DBOperation] = []

    def queue_response(self, result: DBResult) -> None:
        """Queue a ``DBResult`` for the next request."""
        self._responses.append(result)

    async def send_and_receive(self, message: object) -> object:
        """Record the request and return the next queued response."""
        self._sent.append(message)
        if not self._responses:
            raise RuntimeError("No response queued")
        return self._responses.pop(0)

    @property
    def sent_operations(self) -> list[DBOperation]:
        """Return all sent DB operations."""
        return self._sent


class TestOrchestratorClient:
    """Verify multiplexed chat/stream IPC behavior."""

    @pytest.mark.asyncio
    async def test_send_chat_returns_response(self) -> None:
        """Sends ``ChatRequest`` and returns ``ChatResponse``."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        fake_ipc.queue_receive(
            IPCChatResponse(
                request_id="r1",
                structured_content=StructuredAssistantContent(answer="Hello!"),
            )
        )

        request = IPCChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        result = await client.send_chat(request, session_id="s1")

        assert isinstance(result, IPCChatResponse)
        assert result.structured_content.answer == "Hello!"
        assert result.request_id == "r1"
        await client.stop()

    @pytest.mark.asyncio
    async def test_send_stop_returns_response(self) -> None:
        """Sends ``StopRequest`` and returns ``StopResponse``."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()
        client.register_chat_request("chat-1", "s1", run_id="run-1")
        client._chat_send_events["chat-1"].set()

        fake_ipc.queue_receive(IPCStopResponse(request_id="r1", stopped=True))

        result = await client.send_stop(
            IPCStopRequest(
                request_id="r1",
                target_request_id="chat-1",
                run_id="run-1",
                session_id="s1",
            )
        )
        assert result.stopped is True
        await client.stop()

    @pytest.mark.asyncio
    async def test_send_stop_waits_for_authoritative_chat_response(self) -> None:
        """Stop acknowledgement does not synthesize a chat outcome locally."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        chat_request = IPCChatRequest(
            request_id="chat-1",
            run_id="run-chat-1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        chat_task = asyncio.create_task(client.send_chat(chat_request, session_id="s1"))
        await asyncio.sleep(0)
        assert client.is_processing

        fake_ipc.queue_receive(IPCStopResponse(request_id="stop-1", stopped=True))
        stop_response = await client.send_stop(
            IPCStopRequest(
                request_id="stop-1",
                target_request_id="chat-1",
                run_id="run-chat-1",
                session_id="s1",
            )
        )
        assert client.is_processing
        assert not chat_task.done()
        fake_ipc.queue_receive(
            IPCChatResponse(
                request_id="chat-1",
                outcome_kind="cancelled",
                run_id="run-chat-1",
            )
        )
        chat_response = await asyncio.wait_for(chat_task, timeout=1.0)

        assert stop_response.stopped is True
        assert chat_response.request_id == "chat-1"
        assert chat_response.outcome_kind.value == "cancelled"
        assert chat_response.run_id == "run-chat-1"
        assert not client.is_processing
        await client.stop()

    @pytest.mark.asyncio
    async def test_stop_command_uses_distinct_correlation_id(self) -> None:
        """Stop response cannot overwrite the target chat response future."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        stop_request = IPCStopRequest(
            request_id="stop-1",
            target_request_id="chat-1",
            run_id="run-chat-1",
            session_id="s1",
        )
        client.register_chat_request("chat-1", "s1", run_id="run-chat-1")
        client._chat_send_events["chat-1"].set()
        fake_ipc.queue_receive(IPCStopResponse(request_id="stop-1", stopped=True))
        stop_response = await client.send_stop(stop_request)
        assert stop_response.stopped is True
        assert fake_ipc.sent_messages == [stop_request]
        await client.stop()

    @pytest.mark.asyncio
    async def test_stream_event_falls_back_to_reconnected_conversation(
        self,
    ) -> None:
        """Route final events to a reconnected session for the same chat."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        old_queue: asyncio.Queue = asyncio.Queue()
        new_queue: asyncio.Queue = asyncio.Queue()
        client.register_session_queue("old-session", old_queue, conversation_id="c1")
        client.register_chat_request("chat-1", "old-session", "c1", run_id="run-chat-1")
        client.unregister_session_queue("old-session")
        client.register_session_queue("new-session", new_queue, conversation_id="c1")

        event = StreamEvent(
            request_id="chat-1",
            event_type=StreamEventType.FINAL,
            data={"content": "done"},
        )
        await client._handle_stream_event(event)

        routed_event = await asyncio.wait_for(new_queue.get(), timeout=1.0)
        assert routed_event is event
        assert old_queue.empty()
        await client.stop()

    @pytest.mark.asyncio
    async def test_stale_realtime_disconnect_does_not_remove_new_queue(
        self,
    ) -> None:
        """A stale browser connection cannot unregister a newer queue."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        old_queue: asyncio.Queue = asyncio.Queue()
        new_queue: asyncio.Queue = asyncio.Queue()
        client.register_session_queue("session-1", old_queue, conversation_id="c1")
        client.register_session_queue("session-1", new_queue, conversation_id="c1")
        client.unregister_session_queue("session-1", old_queue)

        event = StreamEvent(
            request_id="",
            event_type=StreamEventType.CONVERSATION_UPDATED,
            data={"conversation_id": "c1"},
        )
        await client.publish_conversation_event(
            conversation_id="c1",
            event_type=event.event_type,
            data=event.data,
        )

        routed_event = await asyncio.wait_for(new_queue.get(), timeout=1.0)
        assert routed_event.event_type == StreamEventType.CONVERSATION_UPDATED
        await client.stop()

    @pytest.mark.asyncio
    async def test_stream_event_fans_out_to_parallel_same_chat_connections(
        self,
    ) -> None:
        """Parallel browser connections for one chat both receive stream events."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        first_queue: asyncio.Queue = asyncio.Queue()
        second_queue: asyncio.Queue = asyncio.Queue()
        client.register_session_queue("session-1", first_queue, conversation_id="c1")
        client.register_session_queue("session-1", second_queue, conversation_id="c1")
        client.register_chat_request("chat-1", "session-1", "c1", run_id="run-chat-1")

        event = StreamEvent(
            request_id="chat-1",
            event_type=StreamEventType.FINAL,
            data={"content": "done"},
        )
        await client._handle_stream_event(event)

        first_event = await asyncio.wait_for(first_queue.get(), timeout=1.0)
        second_event = await asyncio.wait_for(second_queue.get(), timeout=1.0)
        assert first_event is event
        assert second_event is event
        await client.stop()

    @pytest.mark.asyncio
    async def test_stream_event_does_not_fall_back_to_wrong_chat_in_session(
        self,
    ) -> None:
        """A conversation-scoped event must not leak into another chat."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        wrong_chat_queue: asyncio.Queue = asyncio.Queue()
        client.register_session_queue(
            "session-1",
            wrong_chat_queue,
            conversation_id="c2",
        )
        client.register_chat_request("chat-1", "session-1", "c1", run_id="run-chat-1")

        event = StreamEvent(
            request_id="chat-1",
            event_type=StreamEventType.FINAL,
            data={"content": "done"},
        )
        await client._handle_stream_event(event)

        assert wrong_chat_queue.empty()
        await client.stop()

    @pytest.mark.asyncio
    async def test_send_confirm_returns_response(self) -> None:
        """Sends ``ConfirmRequest`` and returns ``ConfirmResponse``."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        fake_ipc.queue_receive(
            IPCConfirmResponse(request_id="r1", action_id="a1", accepted=True)
        )

        result = await client.send_confirm(
            IPCConfirmRequest(
                request_id="r1",
                action_id="a1",
                decision="approve",
            )
        )
        assert result.accepted is True
        await client.stop()

    @pytest.mark.asyncio
    async def test_send_reload_llm_returns_response(self) -> None:
        """Sends the reload request and returns the reload response."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        fake_ipc.queue_receive(
            ReloadLLMResponse(
                request_id="r1",
                success=True,
                active_provider="ollama",
                model="qwen3.5:9b",
            )
        )

        result = await client.send_reload_llm("r1")

        assert result.success is True
        assert result.active_provider == "ollama"
        assert result.model == "qwen3.5:9b"
        await client.stop()

    @pytest.mark.asyncio
    async def test_stream_events_routed_to_session_queue(self) -> None:
        """Routes stream events into the registered session queue."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        client.register_session_queue("s1", queue)

        request = IPCChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )

        fake_ipc.queue_receive(
            StreamEvent(
                request_id="r1",
                event_type=StreamEventType.TOKEN,
                data="Hello",
            )
        )
        fake_ipc.queue_receive(
            IPCChatResponse(
                request_id="r1",
                structured_content=StructuredAssistantContent(answer="Hello!"),
            )
        )

        result = await client.send_chat(request, session_id="s1")
        assert result.structured_content.answer == "Hello!"

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, StreamEvent)
        assert event.data == "Hello"

        client.unregister_session_queue("s1")
        await client.stop()

    @pytest.mark.asyncio
    async def test_is_processing_flag(self) -> None:
        """Sets and clears ``is_processing`` around a chat request."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        assert not client.is_processing

        async def delayed_response() -> None:
            """Assert the processing flag while the request is in flight."""
            await asyncio.sleep(0.05)
            assert client.is_processing
            fake_ipc.queue_receive(
                IPCChatResponse(
                    request_id="r1",
                    structured_content=StructuredAssistantContent(answer="ok"),
                )
            )

        response_task = asyncio.create_task(delayed_response())
        request = IPCChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        await client.send_chat(request, session_id="s1")
        await response_task

        assert not client.is_processing
        await client.stop()

    @pytest.mark.asyncio
    async def test_disconnected_raises(self) -> None:
        """Raises ``IPCChannelError`` when the client is not connected."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)

        request = IPCChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        with pytest.raises(IPCChannelError, match="Not connected"):
            await client.send_chat(request, session_id="s1")

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        """Raises ``TimeoutError`` when no response arrives in time."""
        fake_ipc = FakeOrchestratorIPC()
        client = OrchestratorClient(fake_ipc)
        await client.start()

        request = IPCChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        with pytest.raises(asyncio.TimeoutError):
            await client.send_chat(request, session_id="s1", timeout=0.05)
        await client.stop()


class TestAdminDBClient:
    """Verify DB Proxy Socket-B admin client behavior."""

    @pytest.fixture
    def fake_ipc(self) -> FakeDBProxyIPC:
        """Create a fake DB-proxy IPC client."""
        return FakeDBProxyIPC()

    @pytest.fixture
    def admin_client(self, fake_ipc: FakeDBProxyIPC) -> AdminDBClient:
        """Create an ``AdminDBClient`` backed by the fake IPC client."""
        return AdminDBClient(fake_ipc)

    @pytest.mark.asyncio
    async def test_get_signing_material(
        self,
        admin_client: AdminDBClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Loads signing material through socket B."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"key_material": "aa" * 32, "salt": "bb" * 16},
            )
        )
        result = await admin_client.get_signing_material()
        assert "key_material" in result
        assert fake_ipc.sent_operations[0].operation == "get_signing_material"

    @pytest.mark.asyncio
    async def test_delete_conversation(
        self,
        admin_client: AdminDBClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Deletes a conversation through socket B."""
        fake_ipc.queue_response(DBResult(request_id="r1", status="success", data={}))
        await admin_client.delete_conversation("c-1")
        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "delete_conversation"
        assert operation.parameters["conversation_id"] == "c-1"

    @pytest.mark.asyncio
    async def test_list_memory_route_diagnostics(
        self,
        admin_client: AdminDBClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Lists route diagnostics through socket B."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "diagnostics": [
                        {
                            "id": 1,
                            "final_route": {
                                "intent": "user_recall_question",
                                "source": "signal",
                            },
                            "mismatch_label": "no_mismatch",
                        }
                    ]
                },
            )
        )

        items = await admin_client.list_memory_route_diagnostics(
            limit=10,
            offset=5,
            final_intent="user_recall_question",
            mismatch_only=True,
            fallback_only=False,
        )

        assert items[0]["final_route"]["intent"] == "user_recall_question"
        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "list_memory_route_diagnostics"
        assert operation.parameters == {
            "limit": 10,
            "offset": 5,
            "final_intent": "user_recall_question",
            "mismatch_only": True,
            "fallback_only": False,
        }

    @pytest.mark.asyncio
    async def test_get_memory_route_diagnostics_summary(
        self,
        admin_client: AdminDBClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Loads route diagnostics summary through socket B."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "totals": {
                        "total_rows": 4,
                        "fallback_rows": 2,
                        "mismatch_rows": 1,
                        "long_term_rows": 1,
                    },
                    "mismatch_labels": [],
                    "final_intents": [],
                    "fallback_reasons": [],
                },
            )
        )

        result = await admin_client.get_memory_route_diagnostics_summary()

        assert result["totals"]["fallback_rows"] == 2
        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "get_memory_route_diagnostics_summary"
        assert operation.parameters is None

    @pytest.mark.asyncio
    async def test_get_memory_route_diagnostics_summary_with_recent_hours(
        self,
        admin_client: AdminDBClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Passes the requested recent-hours window to socket B."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "totals": {
                        "total_rows": 1,
                        "fallback_rows": 1,
                        "mismatch_rows": 1,
                        "long_term_rows": 0,
                    },
                    "recent_hours": 24,
                    "mismatch_labels": [],
                    "final_intents": [],
                    "fallback_reasons": [],
                },
            )
        )

        result = await admin_client.get_memory_route_diagnostics_summary(
            recent_hours=24,
        )

        assert result["recent_hours"] == 24
        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "get_memory_route_diagnostics_summary"
        assert operation.parameters == {"recent_hours": 24}

    @pytest.mark.asyncio
    async def test_error_raises_http_500(
        self,
        admin_client: AdminDBClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Raises ``HTTPException`` when the DB proxy returns an error."""
        from fastapi import HTTPException

        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="error",
                error_message="table not found",
            )
        )
        with pytest.raises(HTTPException) as exc_info:
            await admin_client.get_stats()
        assert exc_info.value.status_code == 500
        assert "table not found" in exc_info.value.detail


class TestCredentialClient:
    """Verify Credential Proxy client behavior."""

    @pytest.fixture
    def fake_ipc(self) -> FakeDBProxyIPC:
        """Create a fake credential-proxy IPC client."""
        return FakeDBProxyIPC()

    @pytest.fixture
    def credential_client(self, fake_ipc: FakeDBProxyIPC) -> CredentialClient:
        """Create a ``CredentialClient`` backed by the fake IPC client."""
        return CredentialClient(fake_ipc)

    @pytest.mark.asyncio
    async def test_list_integrations(
        self,
        credential_client: CredentialClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Lists integrations through the credential proxy."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"integrations": [{"name": "test", "type": "email"}]},
            )
        )
        items = await credential_client.list_integrations()
        assert len(items) == 1
        assert items[0]["name"] == "test"

    @pytest.mark.asyncio
    async def test_create_integration(
        self,
        credential_client: CredentialClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Creates an integration through the credential proxy."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"integration_id": "int-1"},
            )
        )
        await credential_client.create_integration(
            "test",
            "email",
            {"smtp": "mail.test"},
        )
        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "create_integration"
        assert operation.parameters["name"] == "test"

    @pytest.mark.asyncio
    async def test_delete_integration(
        self,
        credential_client: CredentialClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Deletes an integration through the credential proxy."""
        fake_ipc.queue_response(DBResult(request_id="r1", status="success", data={}))
        await credential_client.delete_integration("test")
        assert fake_ipc.sent_operations[0].operation == "delete_integration"


class TestSigningClient:
    """Verify Signing Proxy client behavior."""

    @pytest.fixture
    def fake_ipc(self) -> FakeDBProxyIPC:
        """Create a fake signing-proxy IPC client."""
        return FakeDBProxyIPC()

    @pytest.fixture
    def signing_client(self, fake_ipc: FakeDBProxyIPC) -> SigningClient:
        """Create a ``SigningClient`` backed by the fake IPC client."""
        return SigningClient(fake_ipc)

    @pytest.mark.asyncio
    async def test_sign_token(
        self,
        signing_client: SigningClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Signs a token through the signing proxy."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"token": "signed.jwt.token"},
            )
        )
        token = await signing_client.sign_token(
            {"jti": "x", "iat": 0, "exp": 0, "gen": 0, "type": "access"}
        )
        assert token == "signed.jwt.token"
        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "sign_token"
        assert operation.parameters["claims"]["jti"] == "x"

    @pytest.mark.asyncio
    async def test_verify_token(
        self,
        signing_client: SigningClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Verifies a token through the signing proxy."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "claims": {
                        "jti": "test",
                        "iat": 1000,
                        "exp": 9999,
                        "gen": 1,
                        "type": "access",
                    }
                },
            )
        )
        claims = await signing_client.verify_token("some.jwt.token")
        assert claims is not None
        assert claims["jti"] == "test"
        assert fake_ipc.sent_operations[0].operation == "verify_token"

    @pytest.mark.asyncio
    async def test_verify_invalid_token_returns_none(
        self,
        signing_client: SigningClient,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Returns ``None`` when token verification fails."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="error",
                error_message="Invalid or expired token",
            )
        )
        result = await signing_client.verify_token("bad.token")
        assert result is None


class TestOrchestratorIPCServerConfirm:
    """Verify ``ConfirmRequest`` handling in the orchestrator IPC server."""

    @pytest.mark.asyncio
    async def test_handle_confirm_approved(self) -> None:
        """Routes approvals to the configured confirm handler."""

        async def confirm_fn(request: ConfirmRequest) -> ConfirmResponse:
            """Return an approved confirmation response."""
            return ConfirmResponse(
                request_id=request.request_id,
                action_id=request.action_id,
                accepted=True,
            )

        async def noop_orchestrator(request: IPCChatRequest) -> None:
            """Provide a no-op orchestrator callback for construction."""
            _ = request

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=noop_orchestrator,
            confirm_fn=confirm_fn,
        )

        request = ConfirmRequest(
            request_id="r1",
            action_id="a1",
            decision="approve",
        )
        response = await server._handle_message("ConfirmRequest", request)

        assert isinstance(response, ConfirmResponse)
        assert response.accepted is True
        assert response.action_id == "a1"

    @pytest.mark.asyncio
    async def test_handle_confirm_no_handler(self) -> None:
        """Returns ``accepted=False`` when no confirm handler is configured."""

        async def noop_orchestrator(request: IPCChatRequest) -> None:
            """Provide a no-op orchestrator callback for construction."""
            _ = request

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=noop_orchestrator,
        )

        request = ConfirmRequest(
            request_id="r1",
            action_id="a1",
            decision="approve",
        )
        response = await server._handle_message("ConfirmRequest", request)

        assert isinstance(response, ConfirmResponse)
        assert response.accepted is False
