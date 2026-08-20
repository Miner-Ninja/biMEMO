"""Tests for Orchestrator process IPC components.

Covers the DBProxyClient wrapper, context/save/audit function factories,
reminder handler routing through IPC, and OrchestratorIPCServer message
handling. Uses mock IPC clients to avoid actual socket connections.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.chat_bridge.service_models import ChatBridgeSyncSkippedError
from backend.ipc.models import (
    BridgeInboundSyncRequest,
    BridgeInboundSyncResponse,
    ChatRequest,
    ChatResponse,
    DBOperation,
    DBResult,
    ReloadLLMRequest,
    ReloadLLMResponse,
    ScheduledToolAuthorizationRequest,
    ScheduledToolAuthorizationResponse,
    StopRequest,
    StopResponse,
    StructuredAssistantContent,
    ToolCatalogRequest,
    ToolCatalogResponse,
)
from backend.processes.orchestrator import (
    DBProxyClient,
    DBProxyError,
    OrchestratorIPCServer,
    make_audit_fn,
    make_save_fn,
)
from backend.processes.orchestrator.bridge_handler import (
    _build_internal_bridge_inbound_url,
)
from backend.processes.orchestrator.dialog_recorder_setup import (
    _select_dialog_recorder_scheduling_mode,
    build_s_zone_compression_llm_call,
)


# --- Test constants ---
TEST_LLM_CONTEXT_WINDOW = 8192


class FakeIPCClient:
    """Mock IPC client that returns preset ``DBResult`` responses."""

    def __init__(self) -> None:
        """Initialize with empty sent-message and response queues."""
        self._responses: list[DBResult] = []
        self._sent: list[DBOperation] = []

    def queue_response(self, result: DBResult) -> None:
        """Queue the next ``DBResult`` returned by ``send_and_receive``."""
        self._responses.append(result)

    async def send_and_receive(self, message: object) -> object:
        """Simulate an IPC round trip using the queued response list."""
        self._sent.append(message)
        if not self._responses:
            raise RuntimeError("No response queued")
        return self._responses.pop(0)

    @property
    def sent_operations(self) -> list[DBOperation]:
        """Return sent DB operations for assertions."""
        return self._sent


@pytest.fixture
def fake_ipc() -> FakeIPCClient:
    """Create a fake IPC client for DB proxy calls."""
    return FakeIPCClient()


@pytest.fixture
def db_client(fake_ipc: FakeIPCClient) -> DBProxyClient:
    """Create a ``DBProxyClient`` backed by the fake IPC client."""
    return DBProxyClient(fake_ipc, timeout=5.0)


class TestDBProxyClient:
    """Verify the typed DB proxy client wrapper."""

    @pytest.mark.asyncio
    async def test_save_message(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Sends ``save_message`` and returns the created message ID."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"message_id": "m-123"},
            )
        )
        message_id = await db_client.save_message("c1", "user", "hello")
        assert message_id == "m-123"
        assert fake_ipc.sent_operations[0].operation == "save_message"

    @pytest.mark.asyncio
    async def test_load_recent_messages(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Parses the recent-messages response payload."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"},
                    ]
                },
            )
        )
        messages = await db_client.load_recent_messages("c1")
        assert len(messages) == 2
        assert messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_list_records(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Sends ``list_records`` and returns parsed record rows."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r2",
                status="success",
                data={"records": [{"id": "record-1", "summary": "saved fact"}]},
            )
        )

        records = await db_client.list_records(limit=10, offset=3)

        assert records == [{"id": "record-1", "summary": "saved fact"}]
        assert fake_ipc.sent_operations[0].operation == "list_records"
        assert fake_ipc.sent_operations[0].parameters == {"limit": 10, "offset": 3}

    @pytest.mark.asyncio
    async def test_count_memory_entries(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Sends ``count_memory_entries`` and returns integer counts."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r3",
                status="success",
                data={"messages": 4, "records": 2, "assertions": 1},
            )
        )

        counts = await db_client.count_memory_entries()

        assert counts == {"messages": 4, "records": 2, "assertions": 1}
        assert fake_ipc.sent_operations[0].operation == "count_memory_entries"


class TestOrchestratorBridgeInbound:
    """Verify bridge inbound IPC errors stay local to one request."""

    @pytest.mark.asyncio
    async def test_bridge_inbound_skip_returns_unsynced_reason(self) -> None:
        """Return one controlled skip result without breaking the IPC channel."""

        async def orchestrator_fn(request: ChatRequest) -> ChatResponse:
            """Return a minimal successful chat response."""
            return ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="ok"),
            )

        async def bridge_inbound_fn(request: BridgeInboundSyncRequest) -> str:
            """Simulate one missing bridge link lookup."""
            del request
            raise ChatBridgeSyncSkippedError("bridge_not_found")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test-orchestrator-ipc.sock",
            secret=b"x" * 32,
            orchestrator_fn=orchestrator_fn,
            bridge_inbound_fn=bridge_inbound_fn,
        )

        response = await server._handle_message(
            "BridgeInboundSyncRequest",
            BridgeInboundSyncRequest(
                request_id="bridge-1",
                provider="discord",
                external_space_id="guild-1",
                external_channel_id="channel-1",
                external_message_id="message-1",
                author_id="user-1",
                author_name="Ops",
                content="hello",
            ),
        )

        assert response == BridgeInboundSyncResponse(
            request_id="bridge-1",
            conversation_id="",
            message_id="",
            synced=False,
            reason="bridge_not_found",
        )


class TestDialogRecorderCompressionCall:
    """Verify S-zone compression uses the dialog recorder model wiring."""

    @pytest.mark.asyncio
    async def test_build_s_zone_compression_llm_call_uses_dialog_model(self) -> None:
        """Build one compression callable that forwards to provider.chat."""
        provider = SimpleNamespace(
            dialog_recorder_model="dialog-model",
            chat=AsyncMock(
                return_value=SimpleNamespace(content="Goal: keep a summary")
            ),
        )

        compression_call = build_s_zone_compression_llm_call(provider)

        assert compression_call is not None

        result = await compression_call(
            [{"role": "user", "content": "Summarize this"}],
            123,
        )

        assert result.content == "Goal: keep a summary"
        provider.chat.assert_awaited_once()
        messages = provider.chat.await_args.args[0]
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].content == "Summarize this"
        assert provider.chat.await_args.kwargs["model"] == "dialog-model"
        assert provider.chat.await_args.kwargs["max_tokens"] == 123

    @pytest.mark.asyncio
    async def test_reload_llm_request_returns_reload_response(self) -> None:
        """Dispatch one reload request through the IPC server."""

        async def orchestrator_fn(request: ChatRequest) -> ChatResponse:
            """Return a minimal successful chat response."""
            return ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="ok"),
            )

        async def reload_llm_fn() -> ReloadLLMResponse:
            """Return a successful reload result."""
            return ReloadLLMResponse(
                request_id="",
                success=True,
                active_provider="ollama",
                model="qwen3.5:9b",
            )

        server = OrchestratorIPCServer(
            socket_path="/tmp/test-orchestrator-ipc.sock",
            secret=b"x" * 32,
            orchestrator_fn=orchestrator_fn,
            reload_llm_fn=reload_llm_fn,
        )

        response = await server._handle_message(
            "ReloadLLMRequest",
            ReloadLLMRequest(request_id="reload-1"),
        )

        assert response == ReloadLLMResponse(
            request_id="reload-1",
            success=True,
            active_provider="ollama",
            model="qwen3.5:9b",
            error=None,
        )

    @pytest.mark.asyncio
    async def test_bridge_inbound_internal_error_returns_internal_reason(self) -> None:
        """Return one explicit internal error result without breaking IPC."""

        async def orchestrator_fn(request: ChatRequest) -> ChatResponse:
            """Return a minimal successful chat response."""
            return ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="ok"),
            )

        async def bridge_inbound_fn(request: BridgeInboundSyncRequest) -> str:
            """Simulate one unexpected internal bridge failure."""
            del request
            raise RuntimeError("broken")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test-orchestrator-ipc.sock",
            secret=b"x" * 32,
            orchestrator_fn=orchestrator_fn,
            bridge_inbound_fn=bridge_inbound_fn,
        )

        response = await server._handle_message(
            "BridgeInboundSyncRequest",
            BridgeInboundSyncRequest(
                request_id="bridge-1",
                provider="discord",
                external_space_id="guild-1",
                external_channel_id="channel-1",
                external_message_id="message-1",
                author_id="user-1",
                author_name="Ops",
                content="hello",
            ),
        )

        assert response == BridgeInboundSyncResponse(
            request_id="bridge-1",
            conversation_id="",
            message_id="",
            synced=False,
            reason="internal_error",
        )


class TestBridgeInboundUrl:
    """Verify internal bridge URL building for messenger intake."""

    def test_build_internal_bridge_inbound_url_uses_loopback(self) -> None:
        """Build the bridge URL with loopback instead of 0.0.0.0."""
        settings = SimpleNamespace(port=8000)

        url = _build_internal_bridge_inbound_url(settings)

        assert url == "http://127.0.0.1:8000"

    @pytest.mark.asyncio
    async def test_load_context(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Loads combined context payloads through IPC."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "summaries": [
                        {
                            "main_point": "pattern1",
                            "created_at": "2026-01-15T10:00:00.000",
                        }
                    ],
                    "records": [{"id": "o1", "summary": "obs1", "key_facts": "[]"}],
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        )
        context = await db_client.load_context("s1", conversation_id="conv-1")
        assert len(context["summaries"]) == 1
        assert len(context["records"]) == 1
        assert len(context["messages"]) == 1
        assert fake_ipc.sent_operations[0].parameters["conversation_id"] == "conv-1"

    @pytest.mark.asyncio
    async def test_write_audit(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Returns the audit entry ID from ``write_audit``."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"entry_id": 42},
            )
        )
        entry_id = await db_client.write_audit(
            "tool_call",
            tool_name="file_read",
        )
        assert entry_id == 42

    @pytest.mark.asyncio
    async def test_verify_chain(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Returns ``True`` when the audit chain is valid."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"valid": True},
            )
        )
        is_valid = await db_client.verify_chain()
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_save_record(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Returns the new record ID from ``save_record``."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"record_id": "o-123"},
            )
        )
        record_id = await db_client.save_record(
            "c1",
            "s1",
            "summary",
            source_message_ids=["m1"],
        )
        assert record_id == "o-123"
        assert fake_ipc.sent_operations[0].parameters["source_message_ids"] == ["m1"]

    @pytest.mark.asyncio
    async def test_get_stats(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Loads DB stats through the client wrapper."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"messages": 100, "conversations": 5},
            )
        )
        stats = await db_client.get_stats()
        assert stats["messages"] == 100

    @pytest.mark.asyncio
    async def test_error_response_raises(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Raises ``DBProxyError`` when the proxy returns an error."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="error",
                error_message="table not found",
            )
        )
        with pytest.raises(DBProxyError, match="table not found"):
            await db_client.save_message("c1", "user", "hi")

    @pytest.mark.asyncio
    async def test_timeout_raises(self, fake_ipc: FakeIPCClient) -> None:
        """Raises ``DBProxyError`` when the IPC layer times out."""

        async def slow_send(message: object) -> object:
            """Simulate a DB proxy call that exceeds the timeout."""
            await asyncio.sleep(10)
            return message

        fake_ipc.send_and_receive = slow_send
        client = DBProxyClient(fake_ipc, timeout=0.05)
        with pytest.raises(DBProxyError, match="timed out"):
            await client.save_message("c1", "user", "hi")


class TestSaveFn:
    """Verify ``make_save_fn`` behavior."""

    @pytest.mark.asyncio
    async def test_save_fn_sends_operation(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Sends a ``save_message`` operation via the DB proxy."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"message_id": "m1"},
            )
        )

        save_fn = make_save_fn(db_client)
        await save_fn("c1", "user", "hello")

        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "save_message"
        assert operation.parameters["conversation_id"] == "c1"


class TestAuditFn:
    """Verify ``make_audit_fn`` behavior."""

    @pytest.mark.asyncio
    async def test_audit_fn_sends_write_audit(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Sends a ``write_audit`` operation through the DB proxy."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"entry_id": 1},
            )
        )

        audit_fn = make_audit_fn(db_client)
        await audit_fn("tool_success", "file_read", zone="GREEN")

        operation = fake_ipc.sent_operations[0]
        assert operation.operation == "write_audit"
        assert operation.parameters["event_type"] == "tool_success"

    @pytest.mark.asyncio
    async def test_audit_fn_swallows_errors(
        self,
        db_client: DBProxyClient,
        fake_ipc: FakeIPCClient,
    ) -> None:
        """Logs DB proxy failures without surfacing them to callers."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="error",
                error_message="db unavailable",
            )
        )

        audit_fn = make_audit_fn(db_client)
        await audit_fn("tool_success", "file_read")


class TestOrchestratorIPCServer:
    """Verify message handling in the orchestrator IPC server."""

    async def _start_chat_and_collect_response(
        self,
        server: OrchestratorIPCServer,
        request: ChatRequest,
    ) -> ChatResponse:
        """Start one chat request and return the pushed async response."""
        server._server.push = AsyncMock()
        response = await server._handle_message("ChatRequest", request)

        assert response is None
        active_task = server._active_task
        assert active_task is not None
        await active_task

        pushed_response = server._server.push.await_args.args[0]
        assert isinstance(pushed_response, ChatResponse)
        return pushed_response

    @pytest.mark.asyncio
    async def test_runtime_tool_catalog_projection(self) -> None:
        """Catalog IPC returns only the orchestrator registry projection."""
        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=AsyncMock(),
            tool_catalog_fn=lambda: [{"name": "web_search", "description": "Web"}],
        )

        response = await server._handle_message(
            "ToolCatalogRequest", ToolCatalogRequest(request_id="catalog-1")
        )

        assert response == ToolCatalogResponse(
            request_id="catalog-1",
            tools=[{"name": "web_search", "description": "Web"}],
        )

    @pytest.mark.asyncio
    async def test_scheduler_authorization_ipc_pins_revision(self) -> None:
        """Scheduler write validation stays inside the orchestrator."""
        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=AsyncMock(),
            scheduled_tool_authorization_fn=lambda names: [
                {"name": names[0], "policy_revision": "registry-v1"}
            ],
        )

        response = await server._handle_message(
            "ScheduledToolAuthorizationRequest",
            ScheduledToolAuthorizationRequest(
                request_id="scheduler-1", tool_names=["web_search"]
            ),
        )

        assert response == ScheduledToolAuthorizationResponse(
            request_id="scheduler-1",
            authorizations=[{"name": "web_search", "policy_revision": "registry-v1"}],
        )

    @pytest.mark.asyncio
    async def test_handle_chat_request(self) -> None:
        """Processes ``ChatRequest`` and pushes ``ChatResponse`` asynchronously."""

        async def orchestrator_fn(request: ChatRequest) -> ChatResponse:
            """Return a trivial chat response for the supplied request."""
            return ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="Hello!"),
            )

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=orchestrator_fn,
        )

        request = ChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        response = await self._start_chat_and_collect_response(server, request)

        assert isinstance(response, ChatResponse)
        assert response.structured_content.answer == "Hello!"
        assert response.request_id == "r1"

    @pytest.mark.asyncio
    async def test_handle_chat_error(self) -> None:
        """Returns an internal-error response when orchestration fails."""

        async def failing_fn(request: ChatRequest) -> ChatResponse:
            """Raise an error to exercise server-side exception handling."""
            raise ValueError("boom")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=failing_fn,
        )

        request = ChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        response = await self._start_chat_and_collect_response(server, request)

        assert isinstance(response, ChatResponse)
        assert "Internal error" in response.structured_content.answer

    @pytest.mark.asyncio
    async def test_handle_chat_error_saves_fallback_assistant_message(self) -> None:
        """Saves the fallback assistant error text when orchestration fails."""
        saved_messages: list[tuple[str, str, str]] = []

        async def failing_fn(request: ChatRequest) -> ChatResponse:
            """Raise an error to exercise fallback message persistence."""
            raise ValueError("boom")

        async def save_error_response_fn(
            conversation_id: str,
            role: str,
            content: str,
        ) -> str:
            """Collect fallback assistant responses for verification."""
            saved_messages.append((conversation_id, role, content))
            return "message-id"

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=failing_fn,
            save_error_response_fn=save_error_response_fn,
        )

        request = ChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        response = await self._start_chat_and_collect_response(server, request)

        assert isinstance(response, ChatResponse)
        # The raw error text ("boom") must not reach the user; the
        # fallback code maps to a stable user-safe message.
        assert response.structured_content.answer == "Internal error. Please try again."
        assert saved_messages == [
            ("c1", "assistant", "Internal error. Please try again.")
        ]

    @pytest.mark.asyncio
    async def test_handle_chat_error_uses_session_id_when_conversation_missing(
        self,
    ) -> None:
        """Falls back to session ID when no conversation ID is available."""
        saved_messages: list[tuple[str, str, str]] = []

        async def failing_fn(request: ChatRequest) -> ChatResponse:
            """Raise an error to exercise the fallback conversation key."""
            raise RuntimeError("broken")

        async def save_error_response_fn(
            conversation_id: str,
            role: str,
            content: str,
        ) -> str:
            """Collect fallback assistant responses for verification."""
            saved_messages.append((conversation_id, role, content))
            return "message-id"

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=failing_fn,
            save_error_response_fn=save_error_response_fn,
        )

        request = ChatRequest(
            request_id="r1",
            run_id="run-r1",
            message="hi",
            channel="web",
            session_id="session-123",
            conversation_id="",
        )
        response = await self._start_chat_and_collect_response(server, request)

        assert isinstance(response, ChatResponse)
        assert response.structured_content.answer == "Internal error. Please try again."
        assert saved_messages == [
            ("session-123", "assistant", "Internal error. Please try again."),
        ]

    @pytest.mark.asyncio
    async def test_handle_stop_no_active_task(self) -> None:
        """Returns ``stopped=False`` when no active task matches the request."""

        async def noop(request: ChatRequest) -> ChatResponse:
            """Return a trivial response for unrelated chat requests."""
            return ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="ok"),
            )

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=noop,
        )

        request = StopRequest(
            request_id="stop-1",
            target_request_id="r1",
            run_id="run-1",
            session_id="s1",
        )
        response = await server._handle_message("StopRequest", request)

        assert isinstance(response, StopResponse)
        assert response.stopped is False

    @pytest.mark.asyncio
    async def test_handle_stop_cancels_active_chat_task(self) -> None:
        """StopRequest cancels the active chat task before it finishes."""
        started = asyncio.Event()

        async def slow_fn(request: ChatRequest) -> ChatResponse:
            """Block until cancelled by the stop request."""
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return ChatResponse(
                    request_id=request.request_id,
                    outcome_kind="cancelled",
                    run_id=request.run_id,
                )
            raise AssertionError("request was not cancelled")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=slow_fn,
        )
        server._server.push = AsyncMock()

        chat_request = ChatRequest(
            request_id="r-stop",
            run_id="run-stop",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        response = await server._handle_message("ChatRequest", chat_request)
        assert response is None
        await started.wait()
        active_task = server._active_task
        assert active_task is not None

        stop_response = await server._handle_message(
            "StopRequest",
            StopRequest(
                request_id="stop-r-stop",
                target_request_id="r-stop",
                run_id="run-stop",
                session_id="s1",
            ),
        )
        assert isinstance(stop_response, StopResponse)
        assert stop_response.stopped is True
        assert server._active_task is None

        await active_task
        pushed_response = server._server.push.await_args_list[-1].args[0]
        assert isinstance(pushed_response, ChatResponse)
        assert pushed_response.request_id == "r-stop"
        assert pushed_response.outcome_kind.value == "cancelled"
        assert pushed_response.run_id == "run-stop"

    @pytest.mark.asyncio
    async def test_handle_stop_confirms_when_task_dies_cancelled(self) -> None:
        """Stop still replies ``stopped=True`` when the chat task ends cancelled.

        The chat handler re-raises CancelledError when Stop lands before the
        turn row exists; the Stop reply must not be lost to that propagation.
        """
        started = asyncio.Event()

        async def uncooperative_fn(request: ChatRequest) -> ChatResponse:
            """Block without absorbing the cancellation."""
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("request was not cancelled")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=uncooperative_fn,
        )
        server._server.push = AsyncMock()

        chat_request = ChatRequest(
            request_id="r-early-stop",
            run_id="run-early-stop",
            message="hi",
            channel="web",
            session_id="s1",
            conversation_id="c1",
        )
        response = await server._handle_message("ChatRequest", chat_request)
        assert response is None
        await started.wait()
        active_task = server._active_task
        assert active_task is not None

        stop_response = await server._handle_message(
            "StopRequest",
            StopRequest(
                request_id="stop-early",
                target_request_id="r-early-stop",
                run_id="run-early-stop",
                session_id="s1",
            ),
        )
        assert isinstance(stop_response, StopResponse)
        assert stop_response.stopped is True
        assert active_task.cancelled()
        assert server._active_task is None

    @pytest.mark.asyncio
    async def test_handle_stop_rejects_mismatched_session(self) -> None:
        """A caller cannot cancel another session's active run."""
        started = asyncio.Event()

        async def slow_fn(request: ChatRequest) -> ChatResponse:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return ChatResponse(
                    request_id=request.request_id,
                    outcome_kind="cancelled",
                    run_id=request.run_id,
                )
            raise AssertionError("request was not cancelled")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=slow_fn,
        )
        server._server.push = AsyncMock()
        chat_request = ChatRequest(
            request_id="chat-1",
            run_id="run-1",
            message="hi",
            channel="web",
            session_id="owner",
            conversation_id="c1",
        )
        await server._handle_message("ChatRequest", chat_request)
        await started.wait()

        response = await server._handle_message(
            "StopRequest",
            StopRequest(
                request_id="stop-1",
                target_request_id="chat-1",
                run_id="run-1",
                session_id="other",
            ),
        )

        assert response.stopped is False
        assert server._active_task is not None
        server._active_task.cancel()
        await server._active_task

    @pytest.mark.asyncio
    async def test_handle_stop_preserves_final_that_wins_cancellation(self) -> None:
        """Stop transport forwards a canonical final instead of replacing it."""
        started = asyncio.Event()

        async def final_wins(request: ChatRequest) -> ChatResponse:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return ChatResponse(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    outcome_kind="final",
                    finalization_id="finalization-1",
                    structured_content=StructuredAssistantContent(answer="committed"),
                )
            raise AssertionError("request was not cancelled")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=final_wins,
        )
        server._server.push = AsyncMock()
        await server._handle_message(
            "ChatRequest",
            ChatRequest(
                request_id="chat-final",
                run_id="run-final",
                message="hi",
                channel="web",
                session_id="owner",
                conversation_id="c1",
            ),
        )
        await started.wait()

        stopped = await server._handle_message(
            "StopRequest",
            StopRequest(
                request_id="stop-final",
                target_request_id="chat-final",
                run_id="run-final",
                session_id="owner",
            ),
        )

        assert stopped.stopped is True
        pushed = server._server.push.await_args_list[-1].args[0]
        assert pushed.outcome_kind.value == "final"
        assert pushed.structured_content.answer == "committed"

    @pytest.mark.asyncio
    async def test_concurrent_stops_cancel_active_task_once(self) -> None:
        """Duplicate exact stops await one canonical cancellation transaction."""
        started = asyncio.Event()
        cancellation_started = asyncio.Event()
        release_cancellation = asyncio.Event()
        cancellation_count = 0

        async def final_after_cas(request: ChatRequest) -> ChatResponse:
            nonlocal cancellation_count
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancellation_count += 1
                cancellation_started.set()
                await release_cancellation.wait()
                return ChatResponse(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    outcome_kind="final",
                    finalization_id="finalization-concurrent",
                    structured_content=StructuredAssistantContent(answer="committed"),
                )
            raise AssertionError("request was not cancelled")

        server = OrchestratorIPCServer(
            socket_path="/tmp/test.sock",
            secret=b"\x00" * 32,
            orchestrator_fn=final_after_cas,
        )
        server._server.push = AsyncMock()
        await server._handle_message(
            "ChatRequest",
            ChatRequest(
                request_id="chat-concurrent",
                run_id="run-concurrent",
                message="hi",
                channel="web",
                session_id="owner",
                conversation_id="c1",
            ),
        )
        await started.wait()
        stop_one = asyncio.create_task(
            server._handle_message(
                "StopRequest",
                StopRequest(
                    request_id="stop-one",
                    target_request_id="chat-concurrent",
                    run_id="run-concurrent",
                    session_id="owner",
                ),
            )
        )
        await cancellation_started.wait()
        stop_two = asyncio.create_task(
            server._handle_message(
                "StopRequest",
                StopRequest(
                    request_id="stop-two",
                    target_request_id="chat-concurrent",
                    run_id="run-concurrent",
                    session_id="owner",
                ),
            )
        )
        await asyncio.sleep(0)
        release_cancellation.set()

        responses = await asyncio.gather(stop_one, stop_two)

        assert cancellation_count == 1
        assert all(response.stopped for response in responses)
        pushed = server._server.push.await_args_list[-1].args[0]
        assert pushed.outcome_kind.value == "final"


class TestDialogRecorderSchedulingSelection:
    """Verify dialog recorder scheduling always stays parallel."""

    def test_parallel_mode_for_separate_dialog_recorder_endpoint(self) -> None:
        """Use parallel scheduling when dialog recorder has a separate endpoint."""
        mode, grace = _select_dialog_recorder_scheduling_mode(
            "http://primary:12434",
            "http://dialog-recorder:12435",
        )
        assert mode == "parallel"
        assert grace == 0.0

    def test_parallel_mode_for_shared_endpoint(self) -> None:
        """Use parallel scheduling even when primary and recorder share endpoint."""
        mode, grace = _select_dialog_recorder_scheduling_mode(
            "http://shared:11434",
            "http://shared:11434",
        )
        assert mode == "parallel"
        assert grace == 0.0
