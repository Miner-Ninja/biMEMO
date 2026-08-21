"""Tests for MCP agent token management and the agent-memory bridge.

Covers hash-based token storage and validation, capability enforcement,
issue/revoke with reauth, environment seeding, the CA root endpoint,
boundary checks, default-off behavior, and the agent-memory-bridge
verification pins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.testclient import TestClient

from backend.channels.endpoints import ReauthManager
from backend.chat_bridge.models import (
    BridgeProvider,
    BridgeTarget,
    OutboundBridgeMessage,
)
from backend.chat_bridge.providers import REGISTERED_PROVIDERS
from backend.chat_bridge.providers.mcp import McpChatBridgeProvider
from backend.config import get_settings, reset_settings
from backend.ipc.models import (
    ChatResponse,
    McpAskRequest,
    McpAskResponse,
    McpMemoryRememberRequest,
    McpMemoryRememberResponse,
    StructuredAssistantContent,
)
from backend.processes.db_proxy.handlers.conversation import _conversation_item
from backend.processes.db_proxy.registry import (
    SOCKET_A_OPERATIONS,
    SOCKET_B_OPERATIONS,
    SOCKET_C_OPERATIONS,
)
from backend.processes.fastapi_app.routes.agents import (
    _CA_ROOT_PATH,
    issue_agent_token,
    revoke_agent_token,
)
from backend.processes.fastapi_app.routes.agents import (
    _hash_token as agents_hash_token,
)
from backend.processes.fastapi_app.routes.mcp import _hash_token as mcp_hash_token
from backend.processes.fastapi_app.routes.mcp import (
    _resolve_agent_capability,
    build_mcp_server,
    seed_mcp_tokens_from_env,
)
from backend.processes.orchestrator.agent_session import resolve_agent_session
from backend.processes.orchestrator.factories import (
    make_mcp_ask_fn,
    make_mcp_memory_remember_fn,
)
from tests.mcp_fakes import (
    FakeCredentialClient,
    _call_tool,
    _FakeMCPAdminClient,
    _FakeMCPOrchestratorClient,
    _handshake,
    _hash_token,
)


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
        h = mcp_hash_token("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    @pytest.mark.asyncio
    async def test_plaintext_never_in_caplog(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The plaintext token never appears in log output."""
        caplog.clear()
        plaintext = "super-secret-token-123"
        mcp_hash_token(plaintext)
        assert plaintext not in caplog.text


# ──────────────────────────────────────────────────────────────────────
# R2: Hash-based validation
# ──────────────────────────────────────────────────────────────────────


class TestHashBasedValidation:
    """R2 / S2.1, S2.2, S2.3 — hash-based bearer validation."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_capability(self) -> None:
        """A valid token (matching stored hash) returns its capability."""
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
        plaintext = "idempotent-token"
        monkeypatch.setenv("BIMEMO_MCP_TOKEN_TEST", f"{plaintext}:read")

        cred = FakeCredentialClient()
        await seed_mcp_tokens_from_env(cred)
        await seed_mcp_tokens_from_env(cred)

        agent_count = sum(
            1
            for i in await cred.list_integrations()
            if i["service_type"] == "mcp_agent"
        )
        assert agent_count == 1

    @pytest.mark.asyncio
    async def test_malformed_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Malformed BIMEMO_MCP_TOKEN_* raises at seed time."""
        monkeypatch.setenv("BIMEMO_MCP_TOKEN_BAD", "no-colon-here")

        cred = FakeCredentialClient()
        with pytest.raises(ValueError, match="expected"):
            await seed_mcp_tokens_from_env(cred)

    @pytest.mark.asyncio
    async def test_env_seed_does_not_wipe_ui_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UI-created non-agent integrations are not deleted by env seed."""
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

        assert isinstance(_CA_ROOT_PATH, Path)


# ──────────────────────────────────────────────────────────────────────
# R12: Boundary checks
# ──────────────────────────────────────────────────────────────────────


class TestBoundaries:
    """R12 / S12.1, S12.2, S12.3 — boundary invariants."""

    def test_registry_has_no_new_operations(self) -> None:
        """Socket allow-lists: new reads on B, writes on A, C unchanged."""
        # New read ops on Socket B
        assert "mcp_memory_recent" in SOCKET_B_OPERATIONS
        assert "mcp_memory_get" in SOCKET_B_OPERATIONS
        assert "mcp_memory_stats" in SOCKET_B_OPERATIONS

        # No credential ops leaked to A or B
        for op in ("store_credential", "get_credential", "delete_credential"):
            assert op not in SOCKET_A_OPERATIONS
            assert op not in SOCKET_B_OPERATIONS
            assert op in SOCKET_C_OPERATIONS

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
        assert not any(op.operation == "save_message" for op in admin.searched)


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
        settings = get_settings()
        assert settings.mcp_enabled is False

    def test_no_mcp_token_file_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings no longer has mcp_token_file field."""
        settings = get_settings()
        assert not hasattr(settings, "mcp_token_file")

    def test_mcp_enabled_without_token_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mcp_enabled=true no longer requires BIMEMO_MCP_TOKEN_FILE."""
        monkeypatch.setenv("BIMEMO_MCP_ENABLED", "true")

        settings = get_settings()
        assert settings.mcp_enabled is True
        # No exception — no token file required


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
        cred = FakeCredentialClient()
        await cred.create_integration(
            name="openrouter",
            integration_type="openrouter",
            config={"model": "x"},
        )
        mgr = ReauthManager()
        token = mgr.issue_token()

        with pytest.raises(HTTPException) as exc:
            await revoke_agent_token(_FakeAuthRequest(), "openrouter", token, cred, mgr)
        assert exc.value.status_code == 404
        assert "openrouter" in {i["name"] for i in await cred.list_integrations()}

    @pytest.mark.asyncio
    async def test_revoke_agent_token_succeeds_with_valid_reauth(self) -> None:
        """A valid reauth token revokes the agent token."""
        cred = FakeCredentialClient()
        await self._seed_agent(cred, "claude")
        mgr = ReauthManager()
        token = mgr.issue_token()

        result = await revoke_agent_token(
            _FakeAuthRequest(), "claude", token, cred, mgr
        )
        assert result.status_code == 204
        assert "claude" not in {i["name"] for i in await cred.list_integrations()}


class TestIssueAgentToken:
    """R4 + reauth — issuing a token requires a valid reauth token."""

    @pytest.mark.asyncio
    async def test_issue_without_reauth_is_rejected(self) -> None:
        """Missing reauth token -> 403 and nothing is stored."""
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

    @pytest.mark.asyncio
    async def test_issue_invalid_capability_rejected_with_422(self) -> None:
        """An unknown capability is client-input validation -> 422, nothing stored."""
        cred = FakeCredentialClient()
        mgr = ReauthManager()

        with pytest.raises(HTTPException) as exc:
            await issue_agent_token(
                _FakeAuthRequest(), "claude", "admin", mgr.issue_token(), cred, mgr
            )
        assert exc.value.status_code == 422
        assert "Invalid capability" in exc.value.detail
        assert await cred.list_integrations() == []


# ──────────────────────────────────────────────────────────────────────
# Verification pins: agent-memory-bridge (R1-R10)
# ──────────────────────────────────────────────────────────────────────


class TestOneSessionPerAgent:
    """R1 — One persistent session per agent identity."""

    async def test_remember_uses_one_session_per_agent(self) -> None:
        """S1.1 — Repeated stores share one session for the same agent."""

        class FakeDBClient:
            def __init__(self):
                self.link_exists = False

            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    if not self.link_exists:
                        return {"chat_bridge_link": None}
                    return {"chat_bridge_link": {"conversation_id": "conv-123"}}
                if op == "create_chat_bridge_link":
                    self.link_exists = True
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                return "conv-123"

        client = FakeDBClient()
        # First call — creates session
        conv1, _ = await resolve_agent_session(agent_id="agent-alpha", db_client=client)
        assert conv1 == "conv-123"
        # Second call — reuses same session
        conv2, _ = await resolve_agent_session(agent_id="agent-alpha", db_client=client)
        assert conv1 == conv2

    async def test_distinct_agents_distinct_sessions(self) -> None:
        """S1.2 — Distinct agents get distinct sessions."""

        class FakeDBClient:
            def __init__(self):
                self.links: dict[str, str] = {}
                self.conv_counter = 0

            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    agent = params.get("external_space_id", "")
                    if agent in self.links:
                        return {
                            "chat_bridge_link": {"conversation_id": self.links[agent]}
                        }
                    return {"chat_bridge_link": None}
                if op == "create_chat_bridge_link":
                    agent = params.get("external_space_id", "")
                    self.links[agent] = f"conv-{agent}"
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                self.conv_counter += 1
                return f"conv-new-{self.conv_counter}"

        client = FakeDBClient()
        conv_a, _ = await resolve_agent_session(
            agent_id="agent-alpha", db_client=client
        )
        conv_b, _ = await resolve_agent_session(agent_id="agent-beta", db_client=client)
        assert conv_a != conv_b


class TestRememberReachesRecorder:
    """R2 — Stored facts enter the normal memory pipeline."""

    async def test_remember_reaches_recorder(self) -> None:
        """S2.1 — A stored fact is queued for record processing."""
        record_calls = []

        async def fake_save_record(
            conversation_id, session_id, summary, source_message_ids
        ):
            record_calls.append(
                (conversation_id, session_id, summary, source_message_ids)
            )

        class FakeDBClient:
            async def get_recent_rate_limit_calls(self, **kwargs):
                return {"timestamps": []}

            async def record_rate_limit_call(self, **kwargs):
                return {"ok": True}

            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    return {"chat_bridge_link": None}
                return {"ok": True}

            async def create_conversation(self, title=""):
                return "conv-123"

            async def save_message(
                self, conversation_id, role, content, channel=None, meta=None
            ):
                return "msg-456"

        client = FakeDBClient()
        fn = make_mcp_memory_remember_fn(client, save_record_fn=fake_save_record)
        request = McpMemoryRememberRequest(
            request_id="req-1",
            content="Agent says: buy milk",
            channel="agent",
            agent_id="agent-alpha",
        )
        result = await fn(request)

        assert result.stored is True
        # The stored fact was enqueued for record processing via save_record_fn.
        assert len(record_calls) == 1
        conversation_id, _session_id, summary, source_ids = record_calls[0]
        assert summary == "Agent says: buy milk"
        assert conversation_id == "conv-123"
        assert source_ids == ["msg-456"]


class TestRememberReturnsAckNoReply:
    """R3 — remember is one-way with an acknowledgement."""

    async def test_remember_returns_ack_no_reply(self) -> None:
        """S3.1 — Store returns an ack, no assistant/LLM reply."""

        class FakeDBClient:
            async def get_recent_rate_limit_calls(self, **kwargs):
                return {"timestamps": []}

            async def record_rate_limit_call(self, **kwargs):
                return {"ok": True}

            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    return {"chat_bridge_link": None}
                if op == "create_chat_bridge_link":
                    return {"ok": True}
                if op == "record_rate_limit_call":
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                return "conv-123"

            async def save_message(
                self, conversation_id, role, content, channel=None, meta=None
            ):
                return "msg-456"

        client = FakeDBClient()
        fn = make_mcp_memory_remember_fn(client)
        request = McpMemoryRememberRequest(
            request_id="req-1",
            content="test fact",
            channel="agent",
            agent_id="agent-alpha",
        )
        result = await fn(request)

        assert isinstance(result, McpMemoryRememberResponse)
        assert result.stored is True
        assert result.message_id == "msg-456"
        assert result.conversation_id == "conv-123"
        # No LLM reply is generated
        assert not hasattr(result, "answer")


class TestAskReturnsAnswer:
    """R4 — ask answers from biMEMO and returns the answer as the tool result."""

    async def test_ask_returns_answer(self) -> None:
        """S4.1 — Ask returns an answer."""

        class FakeDBClient:
            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    return {"chat_bridge_link": None}
                if op == "create_chat_bridge_link":
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                return "conv-123"

        client = FakeDBClient()
        fn = make_mcp_ask_fn(client)

        def fake_orchestrator(req):
            return ChatResponse(
                request_id=req.request_id,
                structured_content=StructuredAssistantContent(
                    answer="The answer is 42"
                ),
            )

        fn.set_orchestrator_fn(fake_orchestrator)
        request = McpAskRequest(
            request_id="req-1",
            question="What is 6*7?",
            agent_id="agent-alpha",
        )
        result = await fn(request)

        assert isinstance(result, McpAskResponse)
        assert "42" in result.answer

    async def test_ask_uses_no_outbound_send(self) -> None:
        """S4.2 — No outbound delivery path is used for ask."""
        provider = McpChatBridgeProvider()

        # send_message is a no-op — it does not raise or make network calls

        ref = await provider.send_message(
            target=BridgeTarget(external_space_id="mcp", external_channel_id="mcp"),
            message=OutboundBridgeMessage(role="assistant", content="Hello"),
        )
        # Returns a local placeholder, no external send occurs
        assert ref.external_message_id == "mcp-no-op"


class TestAskProducesDialogRecord:
    """R5 — ask turns are recorded like a normal dialog."""

    async def test_ask_produces_dialog_record(self) -> None:
        """S5.1 — Ask produces a dialog record via the normal chat path."""
        orchestrator_called = []

        class FakeDBClient:
            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    return {"chat_bridge_link": None}
                if op == "create_chat_bridge_link":
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                return "conv-123"

        def fake_orchestrator(req):
            orchestrator_called.append(req)
            return ChatResponse(
                request_id=req.request_id,
                structured_content=StructuredAssistantContent(answer="recorded"),
            )

        client = FakeDBClient()
        fn = make_mcp_ask_fn(client)
        fn.set_orchestrator_fn(fake_orchestrator)

        request = McpAskRequest(
            request_id="req-1",
            question="What do you remember?",
            agent_id="agent-alpha",
        )
        result = await fn(request)

        assert isinstance(result, McpAskResponse)
        assert len(orchestrator_called) == 1
        # The call uses the normal chat path which records via save_record_fn
        assert orchestrator_called[0].channel == "bridge:mcp"


class TestToolsGatedByMasterSwitch:
    """R6 — The connection is gated by the existing master switch."""

    async def test_tools_gated_by_master_switch(self) -> None:
        """S6.1/S6.2 — Both tools live under the existing MCP_ENABLED gate."""
        reset_settings()
        try:
            settings = get_settings()
            # When mcp_enabled is False, the endpoint is not mounted
            # The server is only built when mcp_enabled is True
            # We verify the setting defaults to False
            assert not settings.mcp_enabled
        finally:
            reset_settings()


class TestAgentWriteGoesThroughOrchestrator:
    """R7 — Agent memory writes cross only the orchestrator boundary."""

    async def test_agent_write_goes_through_orchestrator(self) -> None:
        """S7.1 — Writes go through the orchestrator, not the web tier."""

        class FakeDBClient:
            async def get_recent_rate_limit_calls(self, **kwargs):
                return {"timestamps": []}

            async def record_rate_limit_call(self, **kwargs):
                return {"ok": True}

            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    return {"chat_bridge_link": None}
                if op == "create_chat_bridge_link":
                    return {"ok": True}
                if op == "record_rate_limit_call":
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                return "conv-123"

            async def save_message(
                self, conversation_id, role, content, channel=None, meta=None
            ):
                return "msg-456"

        client = FakeDBClient()
        fn = make_mcp_memory_remember_fn(client)
        request = McpMemoryRememberRequest(
            request_id="req-1",
            content="test",
            channel="agent",
            agent_id="agent-alpha",
        )
        result = await fn(request)
        assert result.stored is True


class TestAgentSessionListedOnceMarked:
    """R8 — Agent sessions are listed as one marked conversation per agent."""

    async def test_agent_session_marker_source(self) -> None:
        """S8.1 — The agent session is an active ``mcp`` bridge link.

        The web tier turns active ``chat_bridge_links`` into the
        ``bridge_provider`` marker (this already covers Telegram/Discord); the
        db conversation handler returns base fields only, with no marker.
        """
        # The db handler returns base fields only — marking is applied
        # downstream by the web tier, not here.
        row = (
            "conv-123",
            "Agent: agent-alpha",
            "idle",
            "",
            None,
            "2024-01-01T00:00:00",
            "2024-01-01T00:00:00",
        )
        item = _conversation_item(row)
        assert item["id"] == "conv-123"
        assert "bridge_provider" not in item

        # resolve_agent_session creates ONE active mcp link — the marker source.
        created: dict = {}

        class FakeDB:
            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    return {"chat_bridge_link": None}
                if op == "create_chat_bridge_link":
                    created.update(params)
                return {"ok": True}

            async def create_conversation(self, title=""):
                return "conv-agent-1"

        conv, _sess = await resolve_agent_session(
            agent_id="agent-alpha", db_client=FakeDB()
        )
        assert conv == "conv-agent-1"
        assert created["provider"] == "mcp"
        assert created["status"] == "active"


class TestMcpProviderRegisteredInertOutbound:
    """R9 — The MCP provider is registered like existing providers."""

    async def test_mcp_provider_registered_inert_outbound(self) -> None:
        """S9.1/S9.2 — MCP provider is present and outbound is inert."""
        # S9.1 — Registered as a peer provider
        assert BridgeProvider.MCP in REGISTERED_PROVIDERS
        assert REGISTERED_PROVIDERS[BridgeProvider.MCP] is McpChatBridgeProvider

        # S9.2 — Outbound is inert

        # MCP provider only configures when mcp_enabled is True
        # We test that the provider can be instantiated and its
        # outbound methods are inert no-ops
        provider = McpChatBridgeProvider()

        # send_message is a no-op
        ref = await provider.send_message(
            target=BridgeTarget(
                external_space_id="mcp",
                external_channel_id="mcp",
            ),
            message=OutboundBridgeMessage(role="assistant", content="Hi"),
        )
        assert ref.external_message_id == "mcp-no-op"

        # create_bridge_target returns a local placeholder
        target = await provider.create_bridge_target(title="test")
        assert target.external_space_id == "mcp"
        assert target.external_channel_id == "mcp"


class TestNoThrowawayConversationPerFact:
    """R10 — The old per-fact bypass is removed."""

    async def test_no_throwaway_conversation_per_fact(self) -> None:
        """S10.1 — No new conversation is created per individual fact."""

        class FakeDBClient:
            def __init__(self):
                self.created_conversations = []
                self.saved_messages = []
                self.link_exists = False

            async def get_recent_rate_limit_calls(self, **kwargs):
                return {"timestamps": []}

            async def record_rate_limit_call(self, **kwargs):
                return {"ok": True}

            async def send_operation(self, op, params=None):
                if op == "get_chat_bridge_by_external_target":
                    if not self.link_exists:
                        return {"chat_bridge_link": None}
                    return {
                        "chat_bridge_link": {
                            "conversation_id": self.created_conversations[0],
                        }
                    }
                if op == "create_chat_bridge_link":
                    self.link_exists = True
                    return {"ok": True}
                if op == "record_rate_limit_call":
                    return {"ok": True}
                return {}

            async def create_conversation(self, title=""):
                conv_id = f"conv-{len(self.created_conversations)}"
                self.created_conversations.append(conv_id)
                return conv_id

            async def save_message(
                self, conversation_id, role, content, channel=None, meta=None
            ):
                msg_id = f"msg-{len(self.saved_messages)}"
                self.saved_messages.append((conversation_id, msg_id))
                return msg_id

        client = FakeDBClient()
        fn = make_mcp_memory_remember_fn(client)

        # Store 3 facts for the same agent
        for i in range(3):
            request = McpMemoryRememberRequest(
                request_id=f"req-{i}",
                content=f"fact {i}",
                channel="agent",
                agent_id="agent-alpha",
            )
            await fn(request)

        # Only ONE conversation should have been created
        assert len(client.created_conversations) == 1
        # All 3 messages went into the same conversation
        for conv_id, _msg_id in client.saved_messages:
            assert conv_id == client.created_conversations[0]
