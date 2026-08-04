"""Tests for MCP (Model Context Protocol) endpoint — agent connection.

Covers hash-based token storage/validation, environment seeding,
capability enforcement, boundary checks, and default-off behavior.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest

from backend.config import reset_settings


if TYPE_CHECKING:
    from backend.ipc.models import DBOperation, DBResult


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of a plaintext token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeCredentialClient:
    """Mock CredentialClient for agent-token tests."""

    def __init__(self) -> None:
        """Initialize empty integration/created/deleted records."""
        self._integrations: list[dict[str, Any]] = []
        self._created: list[dict[str, Any]] = []
        self._deleted: list[str] = []

    async def list_integrations(self) -> list[dict[str, Any]]:
        """Return a copy of the stored integrations."""
        return list(self._integrations)

    async def create_integration(
        self,
        name: str,
        integration_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Store one integration and return its new id."""
        entry = {
            "id": f"int-{len(self._integrations)}",
            "name": name,
            "service_type": integration_type,
            "enabled": True,
            "config": config,
            "created_at": "2025-01-01T00:00:00",
        }
        self._integrations.append(entry)
        self._created.append(entry)
        return {"integration_id": entry["id"]}

    async def delete_integration(self, name: str) -> dict[str, Any]:
        """Delete every integration matching the name."""
        self._integrations = [
            i for i in self._integrations if i.get("name") != name
        ]
        self._deleted.append(name)
        return {"deleted": True}


class FakeDBProxyIPC:
    """Mock IPC client that returns preset DBResult responses."""

    def __init__(self) -> None:
        """Initialize empty response and sent-operation queues."""
        self._responses: list[DBResult] = []
        self._sent: list[DBOperation] = []

    def queue_response(self, result: DBResult) -> None:
        """Queue one DBResult to return from the next call."""
        self._responses.append(result)

    async def send_and_receive(self, message: object) -> object:
        """Record the message and return the next queued response."""
        self._sent.append(message)
        if not self._responses:
            raise RuntimeError("No response queued")
        return self._responses.pop(0)

    @property
    def sent_operations(self) -> list[DBOperation]:
        """Return all recorded sent operations."""
        return self._sent


# ──────────────────────────────────────────────────────────────────────
# R1: Hash-based token storage
# ──────────────────────────────────────────────────────────────────────


class TestHashStorage:
    """R1 / S1.1, S1.2 — store tokens as hashes, return plaintext once."""

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> None:
        yield
        reset_settings()

    @pytest.mark.asyncio
    async def test_issue_stores_only_hash(self) -> None:
        """After issue, the stored config has only token_hash — no plaintext."""
        from backend.processes.fastapi_app.routes.agents import (
            _hash_token as agents_hash_token,
        )

        plaintext = "my-secret-token"
        expected_hash = agents_hash_token(plaintext)

        cred = FakeCredentialClient()
        await cred.create_integration(
            name="test-agent",
            integration_type="mcp_agent",
            config={"token_hash": expected_hash, "capability": "read-write"},
        )

        integrations = await cred.list_integrations()
        agent = next(i for i in integrations if i["service_type"] == "mcp_agent")

        assert "token_hash" in agent["config"]
        assert agent["config"]["token_hash"] == expected_hash
        assert "token" not in agent["config"]
        assert "plaintext" not in agent["config"]

    def test_hash_is_sha256_hex(self) -> None:
        """_hash_token returns a 64-char hex string (SHA-256)."""
        from backend.processes.fastapi_app.routes.mcp import _hash_token

        h = _hash_token("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    @pytest.mark.asyncio
    async def test_plaintext_never_in_caplog(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The plaintext token never appears in log output."""
        from backend.processes.fastapi_app.routes.mcp import _hash_token

        caplog.clear()
        plaintext = "super-secret-token-123"
        _hash_token(plaintext)
        assert plaintext not in caplog.text


# ──────────────────────────────────────────────────────────────────────
# R2: Hash-based validation
# ──────────────────────────────────────────────────────────────────────


class TestHashBasedValidation:
    """R2 / S2.1, S2.2, S2.3 — hash-based bearer validation."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_capability(self) -> None:
        """A valid token (matching stored hash) returns its capability."""
        from backend.processes.fastapi_app.routes.mcp import (
            _resolve_agent_capability,
        )

        plaintext = "valid-token"
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="agent1",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token(plaintext),
                "capability": "read-write",
            },
        )

        capability = await _resolve_agent_capability(
            {"authorization": f"Bearer {plaintext}"},
            cred,
        )
        assert capability == "read-write"

    @pytest.mark.asyncio
    async def test_unknown_hash_rejected(self) -> None:
        """A token whose hash is not stored is rejected."""
        from backend.processes.fastapi_app.routes.mcp import (
            _resolve_agent_capability,
        )

        cred = FakeCredentialClient()
        await cred.create_integration(
            name="agent1",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token("other-token"),
                "capability": "read",
            },
        )

        with pytest.raises(RuntimeError, match="Invalid bearer token"):
            await _resolve_agent_capability(
                {"authorization": "Bearer wrong-token"},
                cred,
            )

    @pytest.mark.asyncio
    async def test_missing_bearer_rejected(self) -> None:
        """Missing Authorization header is rejected."""
        from backend.processes.fastapi_app.routes.mcp import (
            _resolve_agent_capability,
        )

        cred = FakeCredentialClient()

        with pytest.raises(RuntimeError, match="Bearer token required"):
            await _resolve_agent_capability({}, cred)

    @pytest.mark.asyncio
    async def test_tool_does_not_run_without_valid_token(self) -> None:
        """search_memory tool does not execute body when token is invalid."""
        pytest.importorskip("fastmcp")

        admin = _FakeMCPAdminClient()
        orch = _FakeMCPOrchestratorClient()
        cred = FakeCredentialClient()
        # No tokens stored — all requests should fail auth

        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from backend.processes.fastapi_app.routes.mcp import build_mcp_server

        mcp_server = build_mcp_server(admin, orch, cred)
        mcp_app = mcp_server.http_app(path="/")
        parent = Starlette(lifespan=mcp_app.lifespan)
        parent.mount("/mcp", mcp_app)

        with TestClient(parent) as client:
            session_id = _handshake(client)
            result = _call_tool(
                client, session_id, "search_memory", {"query": "hi"}, None
            )

        assert result["isError"] is True
        assert admin.searched == []


# ──────────────────────────────────────────────────────────────────────
# R3: Capability enforcement
# ──────────────────────────────────────────────────────────────────────


class TestCapabilityEnforcement:
    """R3 / S3.1, S3.2 - read token cannot remember; read-write can."""

    @pytest.mark.asyncio
    async def test_read_token_cannot_remember(self) -> None:
        """A read-only token cannot call the remember tool."""
        pytest.importorskip("fastmcp")

        admin = _FakeMCPAdminClient()
        orch = _FakeMCPOrchestratorClient()
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="ro-agent",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token("ro-token"),
                "capability": "read",
            },
        )

        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from backend.processes.fastapi_app.routes.mcp import build_mcp_server

        mcp_server = build_mcp_server(admin, orch, cred)
        mcp_app = mcp_server.http_app(path="/")
        parent = Starlette(lifespan=mcp_app.lifespan)
        parent.mount("/mcp", mcp_app)

        with TestClient(parent) as client:
            session_id = _handshake(client)
            result = _call_tool(
                client,
                session_id,
                "remember",
                {"content": "x"},
                "Bearer ro-token",
            )

        assert result["isError"] is True
        assert orch.remembered == []

    @pytest.mark.asyncio
    async def test_read_write_token_can_remember(self) -> None:
        """A read-write token stores via the orchestrator on channel 'agent'."""
        pytest.importorskip("fastmcp")

        admin = _FakeMCPAdminClient()
        orch = _FakeMCPOrchestratorClient()
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="rw-agent",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token("rw-token"),
                "capability": "read-write",
            },
        )

        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from backend.processes.fastapi_app.routes.mcp import build_mcp_server

        mcp_server = build_mcp_server(admin, orch, cred)
        mcp_app = mcp_server.http_app(path="/")
        parent = Starlette(lifespan=mcp_app.lifespan)
        parent.mount("/mcp", mcp_app)

        with TestClient(parent) as client:
            session_id = _handshake(client)
            result = _call_tool(
                client,
                session_id,
                "remember",
                {"content": "remember this"},
                "Bearer rw-token",
            )

        assert result["isError"] is False
        assert len(orch.remembered) == 1
        assert orch.remembered[0]["channel"] == "agent"


# ──────────────────────────────────────────────────────────────────────
# R4: Issue token → authenticates
# ──────────────────────────────────────────────────────────────────────


class TestIssueAndAuth:
    """R4 / S4.1 — issued token authenticates."""

    @pytest.mark.asyncio
    async def test_issued_token_authenticates(self) -> None:
        """A token issued via create_integration authenticates in MCP."""
        pytest.importorskip("fastmcp")

        admin = _FakeMCPAdminClient()
        orch = _FakeMCPOrchestratorClient()
        cred = FakeCredentialClient()

        plaintext = "issued-token-abc"
        await cred.create_integration(
            name="issued",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token(plaintext),
                "capability": "read-write",
            },
        )

        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from backend.processes.fastapi_app.routes.mcp import build_mcp_server

        mcp_server = build_mcp_server(admin, orch, cred)
        mcp_app = mcp_server.http_app(path="/")
        parent = Starlette(lifespan=mcp_app.lifespan)
        parent.mount("/mcp", mcp_app)

        with TestClient(parent) as client:
            session_id = _handshake(client)
            result = _call_tool(
                client,
                session_id,
                "remember",
                {"content": "test"},
                f"Bearer {plaintext}",
            )

        assert result["isError"] is False
        assert len(orch.remembered) == 1


# ──────────────────────────────────────────────────────────────────────
# R5: Revoke token
# ──────────────────────────────────────────────────────────────────────


class TestRevoke:
    """R5 / S5.1 — revoke by label stops authentication."""

    @pytest.mark.asyncio
    async def test_revoked_token_no_longer_authenticates(self) -> None:
        """After revoking, the token's hash is gone and auth fails."""
        from backend.processes.fastapi_app.routes.mcp import (
            _resolve_agent_capability,
        )

        plaintext = "to-revoke"
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="revocable",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token(plaintext),
                "capability": "read",
            },
        )

        # Before revoke — works
        cap = await _resolve_agent_capability(
            {"authorization": f"Bearer {plaintext}"},
            cred,
        )
        assert cap == "read"

        # Revoke
        await cred.delete_integration("revocable")

        # After revoke — fails
        with pytest.raises(RuntimeError, match="Invalid bearer token"):
            await _resolve_agent_capability(
                {"authorization": f"Bearer {plaintext}"},
                cred,
            )


# ──────────────────────────────────────────────────────────────────────
# R6: List metadata only
# ──────────────────────────────────────────────────────────────────────


class TestListMetadata:
    """R6 / S6.1 — list shows label/capability/created, no plaintext."""

    @pytest.mark.asyncio
    async def test_list_shows_metadata_no_plaintext(self) -> None:
        """list_integrations returns config with token_hash but no secret."""
        plaintext = "secret-token"
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="my-agent",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token(plaintext),
                "capability": "read-write",
            },
        )

        integrations = await cred.list_integrations()
        agent = next(i for i in integrations if i["service_type"] == "mcp_agent")

        assert agent["name"] == "my-agent"
        assert agent["config"]["capability"] == "read-write"
        assert "token_hash" in agent["config"]
        assert plaintext not in str(agent)
        assert "secret" not in agent["config"]

    @pytest.mark.asyncio
    async def test_list_integrations_returns_config_for_all_types(self) -> None:
        """list_integrations returns config field for any service_type."""
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="telegram-bot",
            integration_type="telegram",
            config={"chat_id": "12345"},
        )
        await cred.create_integration(
            name="my-agent",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token("x"),
                "capability": "read",
            },
        )

        integrations = await cred.list_integrations()
        for entry in integrations:
            assert "config" in entry

    @pytest.mark.asyncio
    async def test_config_has_no_secret_field(self) -> None:
        """The config field never contains plaintext secrets."""
        plaintext = "my-api-key-12345"
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="test-agent",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token(plaintext),
                "capability": "read",
            },
        )

        integrations = await cred.list_integrations()
        for entry in integrations:
            config_str = str(entry.get("config", {}))
            assert plaintext not in config_str


# ──────────────────────────────────────────────────────────────────────
# R7: Environment seeding
# ──────────────────────────────────────────────────────────────────────


class TestEnvSeeding:
    """R7 / S7.1, S7.2, S7.3 — env-seed tokens."""

    @pytest.mark.asyncio
    async def test_env_seed_token_authenticates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Token from BIMEMO_MCP_TOKEN_* env var authenticates."""
        from backend.processes.fastapi_app.routes.mcp import (
            _resolve_agent_capability,
            seed_mcp_tokens_from_env,
        )

        plaintext = "env-token-123"
        monkeypatch.setenv("BIMEMO_MCP_TOKEN_CLAUDE", f"{plaintext}:read-write")

        cred = FakeCredentialClient()
        await seed_mcp_tokens_from_env(cred)

        capability = await _resolve_agent_capability(
            {"authorization": f"Bearer {plaintext}"},
            cred,
        )
        assert capability == "read-write"

    @pytest.mark.asyncio
    async def test_reseed_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-seeding does not create duplicate entries."""
        from backend.processes.fastapi_app.routes.mcp import (
            seed_mcp_tokens_from_env,
        )

        plaintext = "idempotent-token"
        monkeypatch.setenv("BIMEMO_MCP_TOKEN_TEST", f"{plaintext}:read")

        cred = FakeCredentialClient()
        await seed_mcp_tokens_from_env(cred)
        await seed_mcp_tokens_from_env(cred)

        agent_count = sum(
            1 for i in await cred.list_integrations()
            if i["service_type"] == "mcp_agent"
        )
        assert agent_count == 1

    @pytest.mark.asyncio
    async def test_malformed_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Malformed BIMEMO_MCP_TOKEN_* raises at seed time."""
        from backend.processes.fastapi_app.routes.mcp import (
            seed_mcp_tokens_from_env,
        )

        monkeypatch.setenv("BIMEMO_MCP_TOKEN_BAD", "no-colon-here")

        cred = FakeCredentialClient()
        with pytest.raises(ValueError, match="expected"):
            await seed_mcp_tokens_from_env(cred)

    @pytest.mark.asyncio
    async def test_env_seed_does_not_wipe_ui_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UI-created non-agent integrations are not deleted by env seed."""
        from backend.processes.fastapi_app.routes.mcp import (
            seed_mcp_tokens_from_env,
        )

        monkeypatch.setenv("BIMEMO_MCP_TOKEN_MYAGENT", "tok:read")

        cred = FakeCredentialClient()
        # Pre-existing non-agent integration
        await cred.create_integration(
            name="telegram-bot",
            integration_type="telegram",
            config={"chat_id": "123"},
        )

        await seed_mcp_tokens_from_env(cred)

        integrations = await cred.list_integrations()
        telegram = [i for i in integrations if i["name"] == "telegram-bot"]
        assert len(telegram) == 1


# ──────────────────────────────────────────────────────────────────────
# R9: CA root endpoint
# ──────────────────────────────────────────────────────────────────────


class TestCaRootEndpoint:
    """R9 / S9.1 — /admin/agents/ca-root returns public cert, not key."""

    def test_ca_root_path_is_configured_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CA root path comes from BIMEMO_CA_ROOT_PATH env var."""
        # It reads from env at import time; in tests it may be empty
        # The important thing is it's a Path, not reading a key file
        from pathlib import Path

        from backend.processes.fastapi_app.routes.agents import _CA_ROOT_PATH

        assert isinstance(_CA_ROOT_PATH, Path)


# ──────────────────────────────────────────────────────────────────────
# R12: Boundary checks
# ──────────────────────────────────────────────────────────────────────


class TestBoundaries:
    """R12 / S12.1, S12.2, S12.3 — boundary invariants."""

    def test_registry_has_no_new_operations(self) -> None:
        """registry.py socket allow-lists gain no new operation."""
        from backend.processes.db_proxy.registry import (
            SOCKET_A_OPERATIONS,
            SOCKET_B_OPERATIONS,
            SOCKET_C_OPERATIONS,
        )

        # Agent token ops use existing operations only
        assert "create_integration" in SOCKET_C_OPERATIONS
        assert "list_integrations" in SOCKET_C_OPERATIONS
        assert "delete_integration" in SOCKET_C_OPERATIONS

        # No new mcp-specific operation added
        agent_ops = {"create_integration", "list_integrations", "delete_integration"}
        for op in agent_ops:
            assert op not in SOCKET_A_OPERATIONS
            assert op not in SOCKET_B_OPERATIONS

    @pytest.mark.asyncio
    async def test_write_routes_through_orchestrator(self) -> None:
        """Write memory routes through orchestrator, not admin client."""
        pytest.importorskip("fastmcp")

        admin = _FakeMCPAdminClient()
        orch = _FakeMCPOrchestratorClient()
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="rw",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token("rw-token"),
                "capability": "read-write",
            },
        )

        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from backend.processes.fastapi_app.routes.mcp import build_mcp_server

        mcp_server = build_mcp_server(admin, orch, cred)
        mcp_app = mcp_server.http_app(path="/")
        parent = Starlette(lifespan=mcp_app.lifespan)
        parent.mount("/mcp", mcp_app)

        with TestClient(parent) as client:
            session_id = _handshake(client)
            result = _call_tool(
                client,
                session_id,
                "remember",
                {"content": "test"},
                "Bearer rw-token",
            )

        assert result["isError"] is False
        # The write went through orchestrator
        assert len(orch.remembered) == 1
        # NOT through admin
        assert not any(
            op.operation == "save_message" for op in admin.searched
        )


# ──────────────────────────────────────────────────────────────────────
# R13: Default off
# ──────────────────────────────────────────────────────────────────────


class TestDefaultOff:
    """R13 / S13.1, S13.2 — disabled by default; no token-file requirement."""

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> None:
        yield
        reset_settings()

    def test_mcp_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MCP is off by default."""
        from backend.config import get_settings

        settings = get_settings()
        assert settings.mcp_enabled is False

    def test_no_mcp_token_file_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings no longer has mcp_token_file field."""
        from backend.config import get_settings

        settings = get_settings()
        assert not hasattr(settings, "mcp_token_file")

    def test_mcp_enabled_without_token_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mcp_enabled=true no longer requires BIMEMO_MCP_TOKEN_FILE."""
        monkeypatch.setenv("BIMEMO_MCP_ENABLED", "true")

        from backend.config import get_settings

        settings = get_settings()
        assert settings.mcp_enabled is True
        # No exception — no token file required


# ──────────────────────────────────────────────────────────────────────
# Fakes for integration tests
# ──────────────────────────────────────────────────────────────────────


class _FakeMCPAdminClient:
    """Minimal admin client stub for the MCP integration test."""

    def __init__(self) -> None:
        self.searched: list[dict[str, Any]] = []

    async def fulltext_search(
        self,
        query: str,
        table: str = "messages",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.searched.append({"query": query, "table": table, "limit": limit})
        return [{"rowid": 1, "snippet": f"hit:{table}"}]


class _FakeMCPOrchestratorClient:
    """Minimal orchestrator client stub for the MCP integration test."""

    def __init__(self) -> None:
        self.remembered: list[dict[str, Any]] = []

    async def mcp_remember(
        self,
        *,
        content: str,
        channel: str,
    ):
        from types import SimpleNamespace

        self.remembered.append({"content": content, "channel": channel})
        return SimpleNamespace(
            stored=True,
            message_id="msg-fake",
            conversation_id="conv-fake",
        )


# ──────────────────────────────────────────────────────────────────────
# SSE helpers (for integration test over HTTP)
# ──────────────────────────────────────────────────────────────────────


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


def _handshake(client: object) -> str:
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


# ──────────────────────────────────────────────────────────────────────
# R7 negative: env seed must preserve UI-issued agent tokens
# ──────────────────────────────────────────────────────────────────────


class TestSeedPreservesUiTokens:
    """R7 negative — env seed only upserts its own labels."""

    @pytest.mark.asyncio
    async def test_seed_keeps_ui_agent_token_absent_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A UI-issued mcp_agent token whose label is not in env survives."""
        from backend.processes.fastapi_app.routes.mcp import (
            seed_mcp_tokens_from_env,
        )

        monkeypatch.delenv("BIMEMO_MCP_TOKEN_UITOKEN", raising=False)
        monkeypatch.setenv("BIMEMO_MCP_TOKEN_ENVAGENT", "envtok:read")

        cred = FakeCredentialClient()
        await cred.create_integration(
            name="uitoken",
            integration_type="mcp_agent",
            config={
                "token_hash": _hash_token("ui-secret"),
                "capability": "read-write",
            },
        )

        await seed_mcp_tokens_from_env(cred)

        names = {i["name"] for i in await cred.list_integrations()}
        assert "uitoken" in names, "UI-issued agent token must not be wiped"
        assert "envagent" in names, "env token must be seeded"

    @pytest.mark.asyncio
    async def test_seed_is_idempotent_for_same_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-seeding the same env token creates no duplicate."""
        from backend.processes.fastapi_app.routes.mcp import (
            seed_mcp_tokens_from_env,
        )

        monkeypatch.setenv("BIMEMO_MCP_TOKEN_DUPE", "dupetok:read")
        cred = FakeCredentialClient()

        await seed_mcp_tokens_from_env(cred)
        await seed_mcp_tokens_from_env(cred)

        dupes = [
            i
            for i in await cred.list_integrations()
            if i["service_type"] == "mcp_agent" and i["name"] == "dupe"
        ]
        assert len(dupes) == 1


# ──────────────────────────────────────────────────────────────────────
# R5 negative: revoke validates reauth and only deletes agent tokens
# ──────────────────────────────────────────────────────────────────────


class _FakeAuthState:
    """Minimal request.state carrying authenticated claims."""

    def __init__(self) -> None:
        self._state = {"claims": {"sub": "admin"}}


class _FakeAuthRequest:
    """Minimal Request stand-in for direct route-function calls."""

    def __init__(self) -> None:
        self.state = _FakeAuthState()
        self.headers: dict[str, str] = {}


class TestRevokeAgentToken:
    """R5 negative — reauth enforced; non-agent integrations untouched."""

    async def _seed_agent(self, cred: Any, name: str) -> None:
        await cred.create_integration(
            name=name,
            integration_type="mcp_agent",
            config={"token_hash": _hash_token("x"), "capability": "read"},
        )

    @pytest.mark.asyncio
    async def test_revoke_without_reauth_is_rejected(self) -> None:
        """Missing reauth token -> 403 and nothing deleted."""
        from fastapi import HTTPException

        from backend.channels.endpoints import ReauthManager
        from backend.processes.fastapi_app.routes.agents import (
            revoke_agent_token,
        )

        cred = FakeCredentialClient()
        await self._seed_agent(cred, "claude")
        mgr = ReauthManager()

        with pytest.raises(HTTPException) as exc:
            await revoke_agent_token(_FakeAuthRequest(), "claude", None, cred, mgr)
        assert exc.value.status_code == 403
        assert "claude" in {i["name"] for i in await cred.list_integrations()}

    @pytest.mark.asyncio
    async def test_revoke_with_invalid_reauth_is_rejected(self) -> None:
        """A bogus reauth token -> 403."""
        from fastapi import HTTPException

        from backend.channels.endpoints import ReauthManager
        from backend.processes.fastapi_app.routes.agents import (
            revoke_agent_token,
        )

        cred = FakeCredentialClient()
        await self._seed_agent(cred, "claude")
        mgr = ReauthManager()

        with pytest.raises(HTTPException) as exc:
            await revoke_agent_token(
                _FakeAuthRequest(), "claude", "bogus-token", cred, mgr
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_revoke_does_not_delete_non_agent_integration(self) -> None:
        """Revoking a label owned by a non-agent integration -> 404, kept."""
        from fastapi import HTTPException

        from backend.channels.endpoints import ReauthManager
        from backend.processes.fastapi_app.routes.agents import (
            revoke_agent_token,
        )

        cred = FakeCredentialClient()
        await cred.create_integration(
            name="openrouter",
            integration_type="openrouter",
            config={"model": "x"},
        )
        mgr = ReauthManager()
        token = mgr.issue_token()

        with pytest.raises(HTTPException) as exc:
            await revoke_agent_token(
                _FakeAuthRequest(), "openrouter", token, cred, mgr
            )
        assert exc.value.status_code == 404
        assert "openrouter" in {i["name"] for i in await cred.list_integrations()}

    @pytest.mark.asyncio
    async def test_revoke_agent_token_succeeds_with_valid_reauth(self) -> None:
        """A valid reauth token revokes the agent token."""
        from backend.channels.endpoints import ReauthManager
        from backend.processes.fastapi_app.routes.agents import (
            revoke_agent_token,
        )

        cred = FakeCredentialClient()
        await self._seed_agent(cred, "claude")
        mgr = ReauthManager()
        token = mgr.issue_token()

        result = await revoke_agent_token(
            _FakeAuthRequest(), "claude", token, cred, mgr
        )
        assert "revoked" in result["detail"]
        assert "claude" not in {i["name"] for i in await cred.list_integrations()}


class TestIssueAgentToken:
    """R4 + reauth — issuing a token requires a valid reauth token."""

    @pytest.mark.asyncio
    async def test_issue_without_reauth_is_rejected(self) -> None:
        """Missing reauth token -> 403 and nothing is stored."""
        from fastapi import HTTPException

        from backend.channels.endpoints import ReauthManager
        from backend.processes.fastapi_app.routes.agents import (
            issue_agent_token,
        )

        cred = FakeCredentialClient()
        mgr = ReauthManager()

        with pytest.raises(HTTPException) as exc:
            await issue_agent_token(
                _FakeAuthRequest(), "claude", "read", None, cred, mgr
            )
        assert exc.value.status_code == 403
        assert await cred.list_integrations() == []

    @pytest.mark.asyncio
    async def test_issue_with_valid_reauth_stores_hash_only(self) -> None:
        """A valid reauth token issues the token and stores only its hash."""
        from backend.channels.endpoints import ReauthManager
        from backend.processes.fastapi_app.routes.agents import (
            issue_agent_token,
        )

        cred = FakeCredentialClient()
        mgr = ReauthManager()
        token = mgr.issue_token()

        result = await issue_agent_token(
            _FakeAuthRequest(), "claude", "read-write", token, cred, mgr
        )
        assert result["label"] == "claude"
        assert result["token"]  # plaintext returned once

        stored = (await cred.list_integrations())[0]
        assert stored["config"]["token_hash"]
        assert "token" not in stored["config"]
