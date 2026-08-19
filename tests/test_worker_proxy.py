"""Tests for worker proxy handler and handler registration.

Covers IPC request routing, error handling, timeout,
and registration of all 9 handlers and tool definitions.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.ipc.protocol import ToolResponse
from backend.tools.broker import ToolBroker
from backend.tools.builtin import (
    EmailSendInput,
    FileReadInput,
    WebSearchInput,
)
from backend.tools.handlers.registration import (
    TOOL_DEFINITIONS,
    register_all_handlers,
    register_tool_definitions,
    validate_all_registered,
)
from backend.tools.handlers.worker_proxy import (
    WorkerProxyError,
    make_email_proxy,
    make_worker_proxy,
)
from backend.tools.registry import PermissionMode, ToolRegistry


class TestWorkerProxy:
    """Tests for make_worker_proxy factory."""

    @pytest.mark.asyncio
    async def test_sends_correct_request(self):
        """Verify proxy sends ToolRequest with correct fields."""
        worker = MagicMock()
        response = ToolResponse(
            request_id="test",
            status="success",
            result={"content": "hello"},
        )
        worker.send_request = AsyncMock(return_value=response)

        handler = make_worker_proxy(worker, "file_read")
        params = FileReadInput(path="/data/test.txt")
        result = await handler(params)

        worker.send_request.assert_called_once()
        request = worker.send_request.call_args[0][0]
        assert request.tool_name == "file_read"
        assert request.parameters["path"] == "/data/test.txt"
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        """Verify successful response returns result string."""
        worker = MagicMock()
        response = ToolResponse(
            request_id="test",
            status="success",
            result={"data": "search results here"},
        )
        worker.send_request = AsyncMock(return_value=response)

        handler = make_worker_proxy(worker, "web_search")
        params = WebSearchInput(query="test query")
        result = await handler(params)
        assert "search results here" in result

    @pytest.mark.asyncio
    async def test_raises_on_worker_error(self):
        """Verify WorkerProxyError raised on error status."""
        worker = MagicMock()
        response = ToolResponse(
            request_id="test",
            status="error",
            error_message="File not found",
        )
        worker.send_request = AsyncMock(return_value=response)

        handler = make_worker_proxy(worker, "file_read")
        params = FileReadInput(path="/nonexistent")
        with pytest.raises(WorkerProxyError, match="File not found"):
            await handler(params)

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        """Verify WorkerProxyError on 30s timeout."""

        async def slow_response(req):
            import asyncio

            await asyncio.sleep(60)

        worker = MagicMock()
        worker.send_request = slow_response

        handler = make_worker_proxy(worker, "file_read")
        params = FileReadInput(path="/data/test.txt")

        import backend.tools.handlers.worker_proxy as wp

        original_timeout = wp.DEFAULT_TOOL_TIMEOUT
        wp.DEFAULT_TOOL_TIMEOUT = 0.01
        try:
            with pytest.raises(WorkerProxyError, match="timeout"):
                await handler(params)
        finally:
            wp.DEFAULT_TOOL_TIMEOUT = original_timeout

    @pytest.mark.asyncio
    async def test_unique_request_id(self):
        """Verify each call gets a unique request_id."""
        worker = MagicMock()
        response = ToolResponse(
            request_id="test",
            status="success",
            result={},
        )
        worker.send_request = AsyncMock(return_value=response)

        handler = make_worker_proxy(worker, "file_read")
        params = FileReadInput(path="/data/a.txt")

        await handler(params)
        await handler(params)

        req1 = worker.send_request.call_args_list[0][0][0]
        req2 = worker.send_request.call_args_list[1][0][0]
        assert req1.request_id != req2.request_id


class TestRegisterAllHandlers:
    """Tests for register_all_handlers."""

    def test_all_handlers_registered(self):
        """Verify all built-in handlers are set on the broker."""
        broker = MagicMock(spec=ToolBroker)
        db = MagicMock()
        fs_worker = MagicMock()
        net_worker = MagicMock()

        register_all_handlers(
            broker=broker,
            db_client=db,
            fs_worker=fs_worker,
            net_worker=net_worker,
            workspace=MagicMock(),
            history_tracker=MagicMock(),
            history_store=MagicMock(),
            data_dir="/data",
            ready_since=0.0,
            get_ollama_status=lambda: "ok",
            get_worker_count=lambda: 2,
        )

        assert broker.bind_handler.call_count == len(TOOL_DEFINITIONS)

        registered = {call.args[0] for call in broker.bind_handler.call_args_list}
        expected = {definition.name for definition in TOOL_DEFINITIONS}
        assert registered == expected

    def test_handlers_are_callable(self):
        """Verify all registered handlers are callable."""
        broker = MagicMock(spec=ToolBroker)
        db = MagicMock()
        fs_worker = MagicMock()
        net_worker = MagicMock()

        register_all_handlers(
            broker=broker,
            db_client=db,
            fs_worker=fs_worker,
            net_worker=net_worker,
            workspace=MagicMock(),
            history_tracker=MagicMock(),
            history_store=MagicMock(),
            data_dir="/data",
            ready_since=0.0,
            get_ollama_status=lambda: "ok",
            get_worker_count=lambda: 2,
        )

        for call in broker.bind_handler.call_args_list:
            handler = call.args[1]
            assert callable(handler)


class TestEmailProxy:
    """Tests for make_email_proxy factory."""

    @pytest.mark.asyncio
    async def test_smtp_config_merged_into_request(self):
        """Verify SMTP config is merged into request params."""
        worker = MagicMock()
        response = ToolResponse(
            request_id="test",
            status="success",
            result={"message_id": "abc123"},
        )
        worker.send_request = AsyncMock(return_value=response)

        smtp_config = {
            "smtp_host": "mail.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_pass": "secret",
            "from_addr": "noreply@example.com",
        }
        handler = make_email_proxy(worker, smtp_config)
        params = EmailSendInput(
            to="dest@example.com",
            subject="Test",
            body="Hello",
        )
        result = await handler(params)

        worker.send_request.assert_called_once()
        request = worker.send_request.call_args[0][0]
        assert request.tool_name == "email_send"
        assert request.parameters["to"] == "dest@example.com"
        assert request.parameters["subject"] == "Test"
        assert request.parameters["body"] == "Hello"
        assert request.parameters["smtp_host"] == "mail.example.com"
        assert request.parameters["smtp_port"] == 587
        assert request.parameters["smtp_user"] == "user@example.com"
        assert request.parameters["smtp_pass"] == "secret"
        assert request.parameters["from_addr"] == "noreply@example.com"
        assert "abc123" in result

    @pytest.mark.asyncio
    async def test_smtp_not_configured_returns_error(self):
        """Verify None smtp_config returns error string."""
        worker = MagicMock()
        handler = make_email_proxy(worker, None)
        params = EmailSendInput(
            to="dest@example.com",
            subject="Test",
            body="Hello",
        )
        result = await handler(params)

        assert result == "Error: SMTP integration not configured"
        worker.send_request.assert_not_called()


class TestValidateAllRegistered:
    """Tests for validate_all_registered."""

    def test_missing_handler_raises(self):
        """Verify RuntimeError when handler is missing."""
        registry = ToolRegistry()
        register_tool_definitions(registry)
        broker = ToolBroker(registry=registry)
        for definition in TOOL_DEFINITIONS[:-1]:
            broker.bind_handler(definition.name, lambda p: p)
        with pytest.raises(RuntimeError, match="missing handlers"):
            validate_all_registered(broker)

    def test_all_present_passes(self):
        """Verify no error when all handlers present."""
        registry = ToolRegistry()
        register_tool_definitions(registry)
        broker = ToolBroker(registry=registry)
        for definition in TOOL_DEFINITIONS:
            broker.bind_handler(definition.name, lambda p: p)
        validate_all_registered(broker)
        assert broker.finalized


class TestRegisterToolDefinitions:
    """Tests for register_tool_definitions."""

    def test_all_nine_definitions_registered(self):
        """Verify all 9 tool definitions are registered."""
        registry = ToolRegistry()
        register_tool_definitions(registry)

        for tool_def in TOOL_DEFINITIONS:
            registered = registry.get(tool_def.name)
            assert registered is not None, f"Missing: {tool_def.name}"
            assert registered.name == tool_def.name

    def test_permission_modes_are_registry_owned(self):
        """Verify representative tools carry backend permission policy."""
        registry = ToolRegistry()
        register_tool_definitions(registry)

        expected_modes = {
            "file_read": PermissionMode.SCOPED,
            "file_write": PermissionMode.PER_INVOCATION,
            "web_search": PermissionMode.CONVERSATION_CAPABILITY,
            "http_fetch": PermissionMode.CONVERSATION_CAPABILITY,
            "email_send": PermissionMode.PER_INVOCATION,
            "system_status": PermissionMode.IMPLICIT,
        }
        for name, mode in expected_modes.items():
            assert registry.get(name).permission.mode == mode

    def test_definitions_have_only_unified_registry_fields(self):
        """Routing metadata is not duplicated in definitions."""
        registry = ToolRegistry()
        register_tool_definitions(registry)

        assert set(registry.get("file_read").__dataclass_fields__) == {
            "name",
            "description",
            "input_model",
            "permission",
        }

    def test_definition_count(self):
        """Verify all built-in definitions exist."""
        assert len(TOOL_DEFINITIONS) == 23
