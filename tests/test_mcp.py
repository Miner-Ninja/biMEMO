"""Tests for MCP (Model Context Protocol) endpoint.

Covers configuration validation, token loading, bearer authentication,
AdminDBClient MCP operations, the resolve_relevant_memory seam, and
middleware exemptions. Uses mock IPC to avoid actual sockets.
"""

from typing import TYPE_CHECKING, Any

import pytest
from backend.config import reset_settings
from backend.ipc.models import DBOperation, DBResult


if TYPE_CHECKING:
    from pathlib import Path


class FakeDBProxyIPC:
    """Mock IPC client that returns preset DBResult responses."""

    def __init__(self) -> None:
        """Initialize empty sent-message and response queues."""
        self._responses: list[DBResult] = []
        self._sent: list[DBOperation] = []

    def queue_response(self, result: DBResult) -> None:
        """Queue a DBResult for the next request."""
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


class TestMCPConfig:
    """Verify MCP configuration validation in Settings."""

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> None:
        """Reset settings singleton after each test."""
        yield
        reset_settings()

    def test_mcp_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MCP endpoint is off when no MCP config is set."""
        from backend.config import get_settings

        settings = get_settings()
        assert settings.mcp_enabled is False
        assert settings.mcp_token_file == ""

    def test_mcp_enabled_requires_token_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Startup fails when MCP is enabled but no token file is set."""
        monkeypatch.setenv("BIMEMO_MCP_ENABLED", "true")
        monkeypatch.delenv("BIMEMO_MCP_TOKEN_FILE", raising=False)

        from backend.config import get_settings
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError, match="BIMEMO_MCP_TOKEN_FILE is required"
        ):
            get_settings()

    def test_mcp_enabled_with_token_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings load when MCP is enabled with a token file path."""
        monkeypatch.setenv("BIMEMO_MCP_ENABLED", "true")
        monkeypatch.setenv("BIMEMO_MCP_TOKEN_FILE", "/run/secrets/mcp_tokens")

        from backend.config import get_settings

        settings = get_settings()
        assert settings.mcp_enabled is True
        assert settings.mcp_token_file == "/run/secrets/mcp_tokens"


class TestMCPTokenLoading:
    """Verify MCP token file parsing and validation."""

    def test_load_valid_tokens(self, tmp_path: Path) -> None:
        """Loads tokens with both capabilities from a valid file."""
        from backend.processes.fastapi_app.routes.mcp import _load_mcp_tokens

        token_file = tmp_path / "tokens.txt"
        token_file.write_text(
            "# biMEMO MCP tokens\n"
            "read-only-token read\n"
            "rw-token read-write\n"
            "\n"
            "# another comment\n"
            "another-read read\n"
        )

        tokens = _load_mcp_tokens(token_file)

        assert tokens["read-only-token"] == "read"
        assert tokens["rw-token"] == "read-write"
        assert tokens["another-read"] == "read"
        assert len(tokens) == 3

    def test_load_single_token(self, tmp_path: Path) -> None:
        """Loads a single token from the file."""
        from backend.processes.fastapi_app.routes.mcp import _load_mcp_tokens

        token_file = tmp_path / "tokens.txt"
        token_file.write_text("single-token read-write\n")

        tokens = _load_mcp_tokens(token_file)

        assert len(tokens) == 1
        assert tokens["single-token"] == "read-write"

    def test_load_empty_file(self, tmp_path: Path) -> None:
        """Returns empty dict for a file with only comments and blank lines."""
        from backend.processes.fastapi_app.routes.mcp import _load_mcp_tokens

        token_file = tmp_path / "tokens.txt"
        token_file.write_text("# only comments\n\n# nothing else\n")

        tokens = _load_mcp_tokens(token_file)

        assert tokens == {}

    def test_reject_invalid_capability(self, tmp_path: Path) -> None:
        """Raises ValueError for an unknown capability value."""
        from backend.processes.fastapi_app.routes.mcp import _load_mcp_tokens

        token_file = tmp_path / "tokens.txt"
        token_file.write_text("my-token admin\n")

        with pytest.raises(ValueError, match="capability 'admin' is invalid"):
            _load_mcp_tokens(token_file)

    def test_reject_missing_capability(self, tmp_path: Path) -> None:
        """Raises ValueError for a line with only a token and no capability."""
        from backend.processes.fastapi_app.routes.mcp import _load_mcp_tokens

        token_file = tmp_path / "tokens.txt"
        token_file.write_text("token-only\n")

        with pytest.raises(
            ValueError,
            match="expected '<token> <capability>'",
        ):
            _load_mcp_tokens(token_file)

    def test_reject_empty_token(self, tmp_path: Path) -> None:
        """Raises ValueError for a line with only a capability and no token."""
        from backend.processes.fastapi_app.routes.mcp import _load_mcp_tokens

        token_file = tmp_path / "tokens.txt"
        token_file.write_text("  read\n")  # single word, splits as 1 part

        with pytest.raises(
            ValueError,
            match="expected '<token> <capability>'",
        ):
            _load_mcp_tokens(token_file)


class TestBearerAuth:
    """Verify bearer token validation logic."""

    @pytest.fixture
    def tokens(self) -> dict[str, str]:
        """Return a token map with one read and one read-write token."""
        return {"read-token": "read", "write-token": "read-write"}

    def test_accept_valid_read_token(self, tokens: dict[str, str]) -> None:
        """Accepts a valid read-only bearer token."""
        from backend.processes.fastapi_app.routes.mcp import (
            _validate_bearer_capability,
        )

        capability = _validate_bearer_capability(
            {"authorization": "Bearer read-token"}, tokens
        )
        assert capability == "read"

    def test_accept_valid_write_token(self, tokens: dict[str, str]) -> None:
        """Accepts a valid read-write bearer token."""
        from backend.processes.fastapi_app.routes.mcp import (
            _validate_bearer_capability,
        )

        capability = _validate_bearer_capability(
            {"authorization": "Bearer write-token"}, tokens
        )
        assert capability == "read-write"

    def test_reject_missing_authorization_header(
        self, tokens: dict[str, str]
    ) -> None:
        """Rejects requests without an Authorization header."""
        from backend.processes.fastapi_app.routes.mcp import (
            _validate_bearer_capability,
        )

        with pytest.raises(RuntimeError, match="Bearer token required"):
            _validate_bearer_capability({}, tokens)

    def test_reject_non_bearer_auth(self, tokens: dict[str, str]) -> None:
        """Rejects non-Bearer authorization schemes."""
        from backend.processes.fastapi_app.routes.mcp import (
            _validate_bearer_capability,
        )

        with pytest.raises(RuntimeError, match="Bearer token required"):
            _validate_bearer_capability({"authorization": "Basic abc123"}, tokens)

    def test_reject_invalid_token(self, tokens: dict[str, str]) -> None:
        """Rejects a bearer token that is not in the token map."""
        from backend.processes.fastapi_app.routes.mcp import (
            _validate_bearer_capability,
        )

        with pytest.raises(RuntimeError, match="Invalid bearer token"):
            _validate_bearer_capability(
                {"authorization": "Bearer unknown-token"}, tokens
            )

    def test_reject_empty_token(self, tokens: dict[str, str]) -> None:
        """Rejects a Bearer header with an empty token string."""
        from backend.processes.fastapi_app.routes.mcp import (
            _validate_bearer_capability,
        )

        with pytest.raises(RuntimeError, match="Empty bearer token"):
            _validate_bearer_capability({"authorization": "Bearer  "}, tokens)

    def test_require_write_capability_allows_read_write(self) -> None:
        """Allows read-write capability through."""
        from backend.processes.fastapi_app.routes.mcp import (
            _require_write_capability,
        )

        _require_write_capability("read-write")  # No exception

    def test_require_write_capability_blocks_read(self) -> None:
        """Blocks read-only capability."""
        from backend.processes.fastapi_app.routes.mcp import (
            _require_write_capability,
        )

        with pytest.raises(RuntimeError, match="Read-only token cannot write"):
            _require_write_capability("read")


class TestAdminDBClientMCPOperations:
    """Verify AdminDBClient fulltext_search method."""

    @pytest.fixture
    def fake_ipc(self) -> FakeDBProxyIPC:
        """Create a fake DB-proxy IPC client."""
        return FakeDBProxyIPC()

    @pytest.fixture
    def admin_client(self, fake_ipc: FakeDBProxyIPC):
        """Create an AdminDBClient backed by the fake IPC client."""
        from backend.processes.fastapi_app.admin_db_client import AdminDBClient

        return AdminDBClient(fake_ipc)

    @pytest.mark.asyncio
    async def test_fulltext_search_messages(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Searches the messages table via FTS5."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "results": [
                        {"rowid": 1, "snippet": "...Python code..."},
                        {"rowid": 2, "snippet": "...database..."},
                    ]
                },
            )
        )

        results = await admin_client.fulltext_search(
            query="python database",
            table="messages",
            limit=10,
        )

        assert len(results) == 2
        assert results[0]["rowid"] == 1
        op = fake_ipc.sent_operations[0]
        assert op.operation == "fulltext_search"
        assert op.parameters["query"] == "python database"
        assert op.parameters["table"] == "messages"
        assert op.parameters["limit"] == 10

    @pytest.mark.asyncio
    async def test_fulltext_search_records(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Searches the records table via FTS5."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "results": [
                        {"rowid": 10, "snippet": "...preference..."},
                    ]
                },
            )
        )

        results = await admin_client.fulltext_search(
            query="preference",
            table="records",
        )

        assert len(results) == 1
        assert results[0]["rowid"] == 10
        op = fake_ipc.sent_operations[0]
        assert op.parameters["table"] == "records"

    @pytest.mark.asyncio
    async def test_fulltext_search_empty_results(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Returns empty list when no results match."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"results": []},
            )
        )

        results = await admin_client.fulltext_search(
            query="nonexistent",
            table="summaries",
        )

        assert results == []
        op = fake_ipc.sent_operations[0]
        assert op.parameters["table"] == "summaries"

    @pytest.mark.asyncio
    async def test_fulltext_search_default_params(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Uses default table='messages' and limit=20."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={"results": []},
            )
        )

        await admin_client.fulltext_search(query="test")

        op = fake_ipc.sent_operations[0]
        assert op.parameters["table"] == "messages"
        assert op.parameters["limit"] == 20


class TestMCPSeam:
    """Verify resolve_relevant_memory seam function."""

    @pytest.fixture
    def fake_ipc(self) -> FakeDBProxyIPC:
        """Create a fake DB-proxy IPC client."""
        return FakeDBProxyIPC()

    @pytest.fixture
    def admin_client(self, fake_ipc: FakeDBProxyIPC):
        """Create an AdminDBClient backed by the fake IPC client."""
        from backend.processes.fastapi_app.admin_db_client import AdminDBClient

        return AdminDBClient(fake_ipc)

    @pytest.mark.asyncio
    async def test_resolve_searches_all_tables(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Calls fulltext_search for each of messages, records, summaries."""
        # Queue 3 responses (one per table)
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "results": [
                        {"rowid": 1, "snippet": "...msg..."},
                    ]
                },
            )
        )
        fake_ipc.queue_response(
            DBResult(
                request_id="r2",
                status="success",
                data={
                    "results": [
                        {"rowid": 2, "snippet": "...record..."},
                    ]
                },
            )
        )
        fake_ipc.queue_response(
            DBResult(
                request_id="r3",
                status="success",
                data={
                    "results": [
                        {"rowid": 3, "snippet": "...summary..."},
                    ]
                },
            )
        )

        from backend.memory.mcp_seam import resolve_relevant_memory

        results = await resolve_relevant_memory(
            admin_client=admin_client,
            query="test query",
            limit=5,
        )

        assert len(results) == 3
        # Each result should be tagged with table and agent_identity
        assert results[0]["table"] == "messages"
        assert results[0]["agent_identity"] == "agent"
        assert results[1]["table"] == "records"
        assert results[1]["agent_identity"] == "agent"
        assert results[2]["table"] == "summaries"
        assert results[2]["agent_identity"] == "agent"

        # Verify 3 operations were sent
        assert len(fake_ipc.sent_operations) == 3
        tables_called = [op.parameters["table"] for op in fake_ipc.sent_operations]
        assert tables_called == ["messages", "records", "summaries"]

    @pytest.mark.asyncio
    async def test_resolve_custom_agent_identity(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Passes custom agent_identity through to results."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "results": [
                        {"rowid": 1, "snippet": "...msg..."},
                    ]
                },
            )
        )
        fake_ipc.queue_response(
            DBResult(
                request_id="r2",
                status="success",
                data={"results": []},
            )
        )
        fake_ipc.queue_response(
            DBResult(
                request_id="r3",
                status="success",
                data={"results": []},
            )
        )

        from backend.memory.mcp_seam import resolve_relevant_memory

        results = await resolve_relevant_memory(
            admin_client=admin_client,
            query="test",
            agent_identity="claude-code",
        )

        assert results[0]["agent_identity"] == "claude-code"

    @pytest.mark.asyncio
    async def test_resolve_empty_results(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Returns empty list when all tables return no results."""
        for _ in range(3):
            fake_ipc.queue_response(
                DBResult(
                    request_id="r1",
                    status="success",
                    data={"results": []},
                )
            )

        from backend.memory.mcp_seam import resolve_relevant_memory

        results = await resolve_relevant_memory(
            admin_client=admin_client,
            query="nothing",
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_resolve_multiple_hits_per_table(
        self,
        admin_client: Any,
        fake_ipc: FakeDBProxyIPC,
    ) -> None:
        """Aggregates multiple hits from each table."""
        fake_ipc.queue_response(
            DBResult(
                request_id="r1",
                status="success",
                data={
                    "results": [
                        {"rowid": 1, "snippet": "...a..."},
                        {"rowid": 2, "snippet": "...b..."},
                    ]
                },
            )
        )
        fake_ipc.queue_response(
            DBResult(
                request_id="r2",
                status="success",
                data={
                    "results": [
                        {"rowid": 3, "snippet": "...c..."},
                    ]
                },
            )
        )
        fake_ipc.queue_response(
            DBResult(
                request_id="r3",
                status="success",
                data={"results": []},
            )
        )

        from backend.memory.mcp_seam import resolve_relevant_memory

        results = await resolve_relevant_memory(
            admin_client=admin_client,
            query="test",
        )

        assert len(results) == 3
        assert results[0]["table"] == "messages"
        assert results[1]["table"] == "messages"
        assert results[2]["table"] == "records"


class TestMCPMiddlewareExemptions:
    """Verify MCP paths are exempt from JWT and CSRF middleware."""

    def test_mcp_in_csrf_exempt_paths(self) -> None:
        """The /mcp path is in the CSRF exemption list."""
        from backend.middleware.csrf import EXEMPT_PATHS

        assert "/mcp" in EXEMPT_PATHS

    def test_mcp_in_jwt_exempt_paths(self) -> None:
        """The /mcp path is in the JWT auth exemption list."""
        from backend.middleware.jwt_auth import EXEMPT_PATHS, EXEMPT_PREFIXES

        assert "/mcp" in EXEMPT_PATHS
        assert "/mcp/" in EXEMPT_PREFIXES

    def test_mcp_subpaths_match_exempt_prefix(self) -> None:
        """MCP subpaths like /mcp/message match the exempt prefix."""
        from backend.middleware.jwt_auth import _is_exempt_path

        assert _is_exempt_path("/mcp")
        assert _is_exempt_path("/mcp/message")
        assert _is_exempt_path("/mcp/anything")
        assert not _is_exempt_path("/admin/memory")


class TestMCPSeamConstants:
    """Verify MCP seam constants match spec requirements."""

    def test_channel_name_is_agent(self) -> None:
        """MCP_CHANNEL_NAME is 'agent' as required by the spec."""
        from backend.memory.mcp_seam import MCP_CHANNEL_NAME

        assert MCP_CHANNEL_NAME == "agent"

    def test_default_search_limit(self) -> None:
        """DEFAULT_SEARCH_LIMIT is 20."""
        from backend.memory.mcp_seam import DEFAULT_SEARCH_LIMIT

        assert DEFAULT_SEARCH_LIMIT == 20

    def test_memory_search_tables(self) -> None:
        """MEMORY_SEARCH_TABLES covers all three FTS tables."""
        from backend.memory.mcp_seam import MEMORY_SEARCH_TABLES

        assert MEMORY_SEARCH_TABLES == ("messages", "records", "summaries")


class _FakeMCPAdminClient:
    """Minimal admin client stub for the MCP integration test."""

    def __init__(self) -> None:
        """Initialize the search-call recorder."""
        self.searched: list[dict[str, Any]] = []

    async def fulltext_search(
        self,
        query: str,
        table: str = "messages",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Record a fulltext_search call and return one fake hit."""
        self.searched.append({"query": query, "table": table, "limit": limit})
        return [{"rowid": 1, "snippet": f"hit:{table}"}]


class _FakeMCPOrchestratorClient:
    """Minimal orchestrator client stub for the MCP integration test.

    The remember write is routed through the orchestrator, not the admin
    (Socket B) client, so this records the mcp_remember calls.
    """

    def __init__(self) -> None:
        """Initialize the remembered-call recorder."""
        self.remembered: list[dict[str, Any]] = []

    async def mcp_remember(self, *, content: str, channel: str):
        """Record an mcp_remember call and return a fake stored result."""
        from types import SimpleNamespace

        self.remembered.append({"content": content, "channel": channel})
        return SimpleNamespace(
            stored=True,
            message_id="msg-fake",
            conversation_id="conv-fake",
        )


def _sse_events(text: str) -> list[dict[str, Any]]:
    """Parse ``data:`` JSON payloads out of an SSE response body."""
    import json

    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class TestMCPIntegration:
    """End-to-end: mount the FastMCP server and drive it over HTTP.

    Covers the wiring that unit tests miss: the lifespan-started session
    manager, tokens flowing from build_mcp_server into the tool closures,
    and header-based capability enforcement.
    """

    def _mounted_client(
        self,
        admin_client: object,
        orchestrator_client: object,
        tokens: dict[str, str],
    ):
        """Return a TestClient with the MCP app mounted and lifespan-wired."""
        from backend.processes.fastapi_app.routes.mcp import build_mcp_server
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        server = build_mcp_server(admin_client, orchestrator_client, tokens)
        mcp_app = server.http_app(path="/")
        parent = Starlette(lifespan=mcp_app.lifespan)
        parent.mount("/mcp", mcp_app)
        return TestClient(parent)

    def _handshake(self, client: object) -> str:
        """Run initialize + initialized and return the session id."""
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        response = client.post("/mcp/", json=init, headers=_MCP_HEADERS)
        session_id = response.headers.get("mcp-session-id")
        client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={**_MCP_HEADERS, "mcp-session-id": session_id},
        )
        return session_id

    def _call_tool(
        self,
        client: object,
        session_id: str,
        name: str,
        arguments: dict[str, Any],
        auth: str | None,
    ) -> dict[str, Any]:
        """Call one tool and return the parsed JSON-RPC result object."""
        headers = {**_MCP_HEADERS, "mcp-session-id": session_id}
        if auth is not None:
            headers["Authorization"] = auth
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = client.post("/mcp/", json=body, headers=headers)
        events = _sse_events(response.text)
        return events[-1]["result"]

    def test_tools_list_excludes_injected_headers(self) -> None:
        """Both tools are listed and the injected headers param is hidden."""
        pytest.importorskip("fastmcp")
        tokens = {"rw": "read-write"}
        with self._mounted_client(
            _FakeMCPAdminClient(), _FakeMCPOrchestratorClient(), tokens
        ) as client:
            session_id = self._handshake(client)
            response = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                headers={**_MCP_HEADERS, "mcp-session-id": session_id},
            )
            tools = {
                tool["name"]: tool
                for tool in _sse_events(response.text)[-1]["result"]["tools"]
            }

        assert set(tools) == {"search_memory", "remember"}
        search_props = tools["search_memory"]["inputSchema"]["properties"]
        assert set(search_props) == {"query", "limit"}
        remember_props = tools["remember"]["inputSchema"]["properties"]
        assert set(remember_props) == {"content", "context"}

    def test_search_with_read_token_delegates(self) -> None:
        """A read token can search; the call reaches fulltext_search."""
        pytest.importorskip("fastmcp")
        admin = _FakeMCPAdminClient()
        with self._mounted_client(
            admin, _FakeMCPOrchestratorClient(), {"ro": "read"}
        ) as client:
            session_id = self._handshake(client)
            result = self._call_tool(
                client, session_id, "search_memory", {"query": "hi"}, "Bearer ro"
            )

        assert result["isError"] is False
        assert [call["table"] for call in admin.searched] == [
            "messages",
            "records",
            "summaries",
        ]

    def test_search_without_token_is_rejected(self) -> None:
        """A search with no bearer token is rejected and does not run."""
        pytest.importorskip("fastmcp")
        admin = _FakeMCPAdminClient()
        with self._mounted_client(
            admin, _FakeMCPOrchestratorClient(), {"ro": "read"}
        ) as client:
            session_id = self._handshake(client)
            result = self._call_tool(
                client, session_id, "search_memory", {"query": "hi"}, None
            )

        assert result["isError"] is True
        assert admin.searched == []

    def test_remember_with_write_token_stores(self) -> None:
        """A read-write token stores via the orchestrator on channel 'agent'."""
        pytest.importorskip("fastmcp")
        orch = _FakeMCPOrchestratorClient()
        with self._mounted_client(
            _FakeMCPAdminClient(), orch, {"rw": "read-write"}
        ) as client:
            session_id = self._handshake(client)
            result = self._call_tool(
                client,
                session_id,
                "remember",
                {"content": "remember this"},
                "Bearer rw",
            )

        assert result["isError"] is False
        assert len(orch.remembered) == 1
        assert orch.remembered[0]["channel"] == "agent"
        assert orch.remembered[0]["content"] == "remember this"

    def test_remember_with_read_token_is_rejected(self) -> None:
        """A read-only token cannot write and nothing is stored."""
        pytest.importorskip("fastmcp")
        orch = _FakeMCPOrchestratorClient()
        with self._mounted_client(
            _FakeMCPAdminClient(), orch, {"ro": "read"}
        ) as client:
            session_id = self._handshake(client)
            result = self._call_tool(
                client, session_id, "remember", {"content": "x"}, "Bearer ro"
            )

        assert result["isError"] is True
        assert orch.remembered == []


class TestMCPConstants:
    """Verify MCP route constants match spec requirements."""

    def test_read_capability_value(self) -> None:
        """MCP_READ_CAPABILITY is 'read'."""
        from backend.processes.fastapi_app.routes.mcp import MCP_READ_CAPABILITY

        assert MCP_READ_CAPABILITY == "read"

    def test_read_write_capability_value(self) -> None:
        """MCP_READ_WRITE_CAPABILITY is 'read-write'."""
        from backend.processes.fastapi_app.routes.mcp import (
            MCP_READ_WRITE_CAPABILITY,
        )

        assert MCP_READ_WRITE_CAPABILITY == "read-write"

    def test_valid_capabilities_set(self) -> None:
        """_VALID_CAPABILITIES contains both allowed values."""
        from backend.processes.fastapi_app.routes.mcp import _VALID_CAPABILITIES

        assert "read" in _VALID_CAPABILITIES
        assert "read-write" in _VALID_CAPABILITIES
        assert len(_VALID_CAPABILITIES) == 2
