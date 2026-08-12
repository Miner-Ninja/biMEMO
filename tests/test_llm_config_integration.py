"""Integration tests for active FastAPI process routes.

Task 12.4: Save LLM config -> read it back -> verify data matches.
Task 12.5: Trigger auth failure, CSRF error, rate limit -> verify audit entries.

Uses the multi-process FastAPI routes with mocked IPC clients.
"""

import secrets
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.channels.endpoints import EndpointRateLimiter, ReauthManager
from backend.channels.jwt_auth import SessionManager
from backend.channels.realtime import RealtimeConnectionManager
from backend.chat_bridge import (
    BridgeDeliveryMode,
    BridgePersonaMode,
    BridgeProvider,
    BridgeStatus,
    BridgeSyncDirection,
    ChatBridgeLink,
)
from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    StructuredAssistantContent,
)
from backend.middleware.csrf import CsrfMiddleware
from backend.middleware.jwt_auth import JwtAuthMiddleware
from backend.middleware.rate_limit import RateLimitMiddleware
from backend.processes.fastapi_app.deps import _csrf_tokens
from backend.processes.fastapi_app.routes import (
    admin_router,
    auth_router,
    chat_bridge_router,
    files_router,
    health_router,
    llm_router,
    memory_router,
)
from backend.processes.fastapi_app.routes.chat import chat_router


# ---------------------------------------------------------------------------
# Helpers: fake signing client and app factory
# ---------------------------------------------------------------------------

_TOKEN_LIFETIME = 3600


class FakeSigningClient:
    """In-memory signing client that issues and verifies tokens without IPC.

    Stores tokens in a dict keyed by token string -> claims dict.
    """

    def __init__(self, session_manager: SessionManager) -> None:
        """Initialize the fake signing client."""
        self._tokens: dict[str, dict] = {}
        self._session_manager = session_manager

    def _make_token(self, claims: dict) -> str:
        """Create one fake signed token and store its claims."""
        token_str = secrets.token_urlsafe(32)
        self._tokens[token_str] = claims
        return token_str

    async def sign_token(self, claims: dict) -> str:
        """Store claims and return a fake token string."""
        return self._make_token(claims)

    async def verify_token(self, token: str) -> dict | None:
        """Return stored claims for the given token."""
        return self._tokens.get(token)

    async def get_auth_status(self) -> dict:
        """Report that initial password setup is already complete."""
        return {"needs_setup": False}

    async def verify_password(self, password: str) -> dict:
        """Simulate password verification via Signing Proxy.

        Accepts 'correct-password', rejects everything else.
        """
        from fastapi import HTTPException

        if password == "correct-password":
            access = await self._session_manager.async_issue_access_token(self)
            refresh = await self._session_manager.async_issue_refresh_token(self)
            return {"access_token": access, "refresh_token": refresh}
        if password == "rate-limited-password":
            raise HTTPException(
                status_code=500,
                detail={"error": "rate_limited", "retry_after": 120},
            )
        raise HTTPException(status_code=500, detail="invalid_password")

    async def check_password(self, password: str) -> dict:
        """Simulate password verification without issuing JWTs."""
        from fastapi import HTTPException

        if password == "correct-password":
            return {"ok": True}
        if password == "rate-limited-password":
            raise HTTPException(
                status_code=500,
                detail={"error": "rate_limited", "retry_after": 120},
            )
        raise HTTPException(status_code=500, detail="invalid_password")

    async def setup_password(self, password: str) -> dict:
        """Reject setup attempts because the password is already configured."""
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="password_already_exists")


def _build_test_app() -> tuple[FastAPI, str, SessionManager, FakeSigningClient, list]:
    """Create a FastAPI app wired for testing with mocked IPC clients.

    Returns:
        (app, access_token, session_manager, signing_client, audit_log)
    """
    session_manager = SessionManager()
    signing_client = FakeSigningClient(session_manager)
    realtime_manager = RealtimeConnectionManager()
    reauth_manager = ReauthManager()
    rate_limiter = EndpointRateLimiter()

    app = FastAPI()

    # Shared audit log (in-memory list) to capture all audit events
    audit_log: list[dict] = []

    async def audit_fn(
        event_type: str,
        tool_name: str | None = None,
        parameters: str | None = None,
        result: str | None = None,
        zone: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Collect one audit entry emitted by middleware or routes."""
        audit_log.append(
            {
                "event_type": event_type,
                "tool_name": tool_name,
                "parameters": parameters,
                "result": result,
                "zone": zone,
                "duration_ms": duration_ms,
            }
        )

    # App state
    app.state.session_manager = session_manager
    app.state.realtime_manager = realtime_manager
    app.state.reauth_manager = reauth_manager
    app.state.rate_limiter = rate_limiter
    app.state.csrf_tokens = _csrf_tokens
    app.state.signing_client = signing_client
    app.state.credential_client = None  # set per-test
    app.state.admin_client = None
    app.state.chat_bridge_service = None
    app.state.settings = SimpleNamespace(
        discord_bridge_token="",
        discord_allowed_user_ids="",
        discord_persona_user_ids="",
        discord_bridge_server_id="",
    )
    app.state.audit_fn = audit_fn

    # Mock orchestrator client (needed for chat routes)
    orchestrator_client = MagicMock()
    orchestrator_client.is_connected = True
    orchestrator_client.is_processing = False
    orchestrator_client.send_bridge_inbound_sync = AsyncMock(
        return_value=SimpleNamespace(
            conversation_id="conversation-1",
            message_id="bridge-inbound-message-1",
            synced=True,
            reason="",
            assistant_content="LLM response text.",
            assistant_message_id="assistant-message-1",
        )
    )
    orchestrator_client.send_reload_llm = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            active_provider="openrouter",
            model="gpt-4o",
            error=None,
        )
    )
    orchestrator_client.publish_conversation_event = AsyncMock()
    app.state.orchestrator_client = orchestrator_client

    # Routes
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(chat_router)
    app.include_router(chat_bridge_router)
    app.include_router(llm_router)
    app.include_router(files_router)
    app.include_router(memory_router)

    # Middleware (reverse order: last added = outermost)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=rate_limiter,
        audit_fn=audit_fn,
    )

    def _get_expected_csrf(jti: str) -> str | None:
        """Return the current CSRF token bound to one session id."""
        return _csrf_tokens.get(jti)

    app.add_middleware(
        CsrfMiddleware,
        get_expected_token=_get_expected_csrf,
        audit_fn=audit_fn,
    )

    app.add_middleware(
        JwtAuthMiddleware,
        session_manager=session_manager,
        signing_client=signing_client,
        audit_fn=audit_fn,
    )

    # Issue a valid access token
    now = time.time()
    claims_dict = {
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + _TOKEN_LIFETIME,
        "gen": session_manager.generation,
        "type": "access",
    }
    access_token = signing_client._make_token(claims_dict)

    return app, access_token, session_manager, signing_client, audit_log


def _authed_client(app: FastAPI, token: str) -> TestClient:
    """Create a TestClient with a valid access token cookie."""
    return TestClient(app, cookies={"access_token": token})


def _authed_client_with_csrf(
    app: FastAPI,
    token: str,
    session_manager: SessionManager,
    signing_client: FakeSigningClient,
) -> tuple[TestClient, str]:
    """Create a TestClient with valid auth and CSRF token.

    Returns:
        (client, csrf_token)
    """
    client = _authed_client(app, token)
    # Get a CSRF token via the endpoint
    response = client.get("/csrf-token")
    assert response.status_code == 200, f"CSRF token fetch failed: {response.text}"
    csrf_token = response.json()["token"]
    return client, csrf_token


# ---------------------------------------------------------------------------
# Fake CredentialClient for LLM config tests
# ---------------------------------------------------------------------------


class FakeCredentialClient:
    """In-memory credential client that stores LLM configs without IPC.

    Simulates create, list, update, delete, activate, and get operations.
    """

    def __init__(self) -> None:
        """Initialize in-memory storage for configs and integrations."""
        self._configs: dict[str, dict] = {}
        self._integrations: dict[str, dict] = {}
        self._next_id = 1

    async def send_operation(
        self,
        operation: str,
        parameters: dict | None = None,
    ) -> dict:
        """Route operations to the appropriate handler."""
        operation_parameters = parameters or {}
        if operation == "create_integration":
            return self._create_integration(operation_parameters)
        if operation == "list_integrations":
            return self._list_integrations()
        if operation == "delete_integration":
            return self._delete_integration(operation_parameters)
        if operation == "create_llm_config":
            return self._create(operation_parameters)
        if operation == "list_llm_configs":
            return self._list()
        if operation == "update_llm_config":
            return self._update(operation_parameters)
        if operation == "delete_llm_config":
            return self._delete(operation_parameters)
        if operation == "activate_llm_config":
            return self._activate(operation_parameters)
        if operation == "get_llm_config_meta":
            return self._get_meta()
        if operation == "get_llm_config_full":
            return self._get_full(operation_parameters)
        return {}

    def _create(self, params: dict) -> dict:
        """Create one fake LLM configuration row."""
        config_id = f"cfg-{self._next_id}"
        self._next_id += 1
        self._configs[config_id] = {
            "id": config_id,
            "service_type": params["service_type"],
            "credentials": params.get("credentials", {}),
            "active": False,
        }
        return {"id": config_id}

    def _list(self) -> dict:
        """List fake LLM configuration metadata without secret values."""
        configs = []
        for cfg in self._configs.values():
            # Return metadata only (no secrets)
            configs.append(
                {
                    "id": cfg["id"],
                    "service_type": cfg["service_type"],
                    "active": cfg["active"],
                    "model": cfg["credentials"].get("model", ""),
                    "base_url": cfg["credentials"].get("base_url", ""),
                    "dialog_recorder_base_url": cfg["credentials"].get(
                        "dialog_recorder_base_url",
                        "",
                    ),
                }
            )
        return {"configs": configs}

    def _update(self, params: dict) -> dict:
        """Update one fake LLM configuration row."""
        config_id = params["config_id"]
        if config_id not in self._configs:
            return {"error": "not_found"}
        self._configs[config_id]["credentials"].update(params.get("credentials", {}))
        return {"detail": "updated"}

    def _delete(self, params: dict) -> dict:
        """Delete one fake LLM configuration row."""
        config_id = params["config_id"]
        if config_id not in self._configs:
            return {"error": "not_found"}
        del self._configs[config_id]
        return {"detail": "deleted"}

    def _activate(self, params: dict) -> dict:
        """Mark one fake configuration active and deactivate the rest."""
        config_id = params["config_id"]
        if config_id not in self._configs:
            return {"error": "not_found"}
        for cfg in self._configs.values():
            cfg["active"] = False
        self._configs[config_id]["active"] = True
        return {"detail": "activated"}

    def _get_meta(self) -> dict:
        """Return metadata for the active fake configuration."""
        for cfg in self._configs.values():
            if cfg["active"]:
                return {
                    "service_type": cfg["service_type"],
                    "model": cfg["credentials"].get("model", ""),
                    "base_url": cfg["credentials"].get("base_url", ""),
                    "dialog_recorder_base_url": cfg["credentials"].get(
                        "dialog_recorder_base_url",
                        "",
                    ),
                }
        return {}

    def _get_full(self, params: dict) -> dict:
        """Return one full fake configuration including credentials."""
        config_id = params["config_id"]
        if config_id not in self._configs:
            return {"error": "not_found"}
        cfg = self._configs[config_id]
        return {
            "service_type": cfg["service_type"],
            "credentials": cfg["credentials"],
            "active": cfg["active"],
        }

    def _create_integration(self, params: dict) -> dict:
        """Create one fake integration definition."""
        self._integrations[params["name"]] = {
            "name": params["name"],
            "type": params["service_type"],
            "config": params.get("config", {}),
        }
        return {"detail": "created"}

    def _list_integrations(self) -> dict:
        """List all fake integration definitions."""
        return {"integrations": list(self._integrations.values())}

    def _delete_integration(self, params: dict) -> dict:
        """Delete one fake integration definition."""
        existed = self._integrations.pop(params["name"], None) is not None
        return {"deleted": existed}

    # Methods that match CredentialClient interface (called by route handlers)

    async def create_llm_config(
        self,
        service_type: str,
        credentials: dict[str, str],
    ) -> dict:
        """Create a new LLM configuration."""
        return await self.send_operation(
            "create_llm_config",
            {
                "service_type": service_type,
                "credentials": credentials,
            },
        )

    async def list_llm_configs(self) -> dict:
        """Return all stored LLM configurations."""
        return await self.send_operation("list_llm_configs", {})

    async def update_llm_config(
        self,
        config_id: str,
        credentials: dict[str, str],
    ) -> dict:
        """Update an existing LLM configuration."""
        return await self.send_operation(
            "update_llm_config",
            {
                "config_id": config_id,
                "credentials": credentials,
            },
        )

    async def delete_llm_config(self, config_id: str) -> dict:
        """Delete an LLM configuration."""
        return await self.send_operation(
            "delete_llm_config",
            {
                "config_id": config_id,
            },
        )

    async def activate_llm_config(self, config_id: str) -> dict:
        """Mark the specified LLM configuration as active."""
        return await self.send_operation(
            "activate_llm_config",
            {
                "config_id": config_id,
            },
        )

    async def get_llm_config_meta(self) -> dict:
        """Return metadata for the active LLM configuration."""
        return await self.send_operation("get_llm_config_meta", {})

    async def get_llm_config_full(self, config_id: str) -> dict:
        """Return full details for a stored LLM configuration."""
        return await self.send_operation(
            "get_llm_config_full",
            {
                "config_id": config_id,
            },
        )

    async def store_llm_config(
        self,
        service_type: str,
        credentials: dict[str, str],
    ) -> dict:
        """Store an LLM configuration through the generic IPC API."""
        return await self.send_operation(
            "store_llm_config",
            {
                "service_type": service_type,
                "credentials": credentials,
            },
        )

    async def create_integration(
        self,
        name: str,
        service_type: str,
        config: dict[str, str],
    ) -> dict:
        """Create an integration definition."""
        return await self.send_operation(
            "create_integration",
            {
                "name": name,
                "service_type": service_type,
                "config": config,
            },
        )

    async def list_integrations(self) -> list[dict]:
        """Return all stored integration definitions."""
        result = await self.send_operation("list_integrations", {})
        return result["integrations"]

    async def delete_integration(self, name: str) -> dict:
        """Delete an integration definition."""
        return await self.send_operation(
            "delete_integration",
            {
                "name": name,
            },
        )


class FakeChatBridgeService:
    """In-memory chat bridge service for admin route tests."""

    def __init__(
        self,
        admin_client: object,
        *,
        allowed_persona_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        """Initialize the fake service with shared bridge storage."""
        self._admin_client = admin_client
        self._allowed_persona_user_ids = allowed_persona_user_ids
        self.created_conversation_ids: list[str] = []
        self.created_bridge_titles: list[str] = []
        self.created_delivery_modes: list[str] = []
        self.created_persona_modes: list[str] = []
        self.created_persona_user_ids: list[str] = []
        self.synced_conversation_ids: list[str] = []
        self.inbound_messages: list[dict[str, str]] = []

    async def create_bridge(
        self,
        *,
        conversation_id: str,
        provider: BridgeProvider,
        created_by: str,
        title: str = "",
        sync_direction: str = "bidirectional",
        delivery_mode: str = "bot_only",
        persona_mode: str = "neutral",
        persona_user_id: str = "",
    ) -> ChatBridgeLink:
        """Create one fake bridge link through the fake admin store."""
        self.created_conversation_ids.append(conversation_id)
        self.created_bridge_titles.append(title)
        self.created_delivery_modes.append(delivery_mode)
        self.created_persona_modes.append(persona_mode)
        self.created_persona_user_ids.append(persona_user_id)
        bridge_link = await self._admin_client.create_chat_bridge_link(
            conversation_id=conversation_id,
            provider=provider.value,
            external_space_id="guild-1",
            external_channel_id="channel-1",
            created_by=created_by,
            sync_direction=sync_direction,
            delivery_mode=delivery_mode,
            persona_mode=persona_mode,
            persona_user_id=persona_user_id,
        )
        return ChatBridgeLink(
            id=str(bridge_link["id"]),
            conversation_id=str(bridge_link["conversation_id"]),
            provider=BridgeProvider(str(bridge_link["provider"])),
            external_space_id=str(bridge_link["external_space_id"]),
            external_channel_id=str(bridge_link["external_channel_id"]),
            created_by=str(bridge_link["created_by"]),
            sync_direction=BridgeSyncDirection(str(bridge_link["sync_direction"])),
            status=BridgeStatus(str(bridge_link["status"])),
            delivery_mode=BridgeDeliveryMode(str(bridge_link["delivery_mode"])),
            persona_mode=BridgePersonaMode(str(bridge_link["persona_mode"])),
            persona_user_id=str(bridge_link["persona_user_id"]),
            created_at=str(bridge_link["created_at"]),
        )

    async def update_bridge_settings(
        self,
        *,
        bridge_link_id: str,
        delivery_mode: str,
        persona_mode: str,
        persona_user_id: str = "",
    ) -> ChatBridgeLink | None:
        """Update one fake bridge link presentation mode."""
        normalized_persona_user_id = persona_user_id.strip()
        if persona_mode == BridgePersonaMode.MAPPED_USER.value:
            if not normalized_persona_user_id:
                raise RuntimeError(
                    "persona_user_id is required when persona_mode is mapped_user"
                )
            if normalized_persona_user_id not in self._allowed_persona_user_ids:
                raise RuntimeError(
                    "persona_user_id is not allowed for Discord bridge persona mapping"
                )
        bridge_link = await self._admin_client.update_chat_bridge_link_settings(
            bridge_link_id=bridge_link_id,
            delivery_mode=delivery_mode,
            persona_mode=persona_mode,
            persona_user_id=normalized_persona_user_id,
        )
        if bridge_link is None:
            return None
        return ChatBridgeLink.from_dict(bridge_link)

    def list_available_providers(self) -> list[BridgeProvider]:
        """Return the configured bridge providers exposed to the web UI."""
        return [BridgeProvider.DISCORD]

    def default_persona_user_id_for_provider(self, provider: BridgeProvider) -> str:
        """Return the single configured default provider user id for tests."""
        if provider != BridgeProvider.DISCORD:
            return ""
        if len(self._allowed_persona_user_ids) != 1:
            return ""
        return next(iter(self._allowed_persona_user_ids))

    async def sync_recent_conversation_messages(
        self,
        *,
        conversation_id: str,
        limit: int = 6,
    ) -> list[object]:
        """Record one outbound sync request for assertions."""
        del limit
        self.synced_conversation_ids.append(conversation_id)
        return []

    async def get_bridge_for_conversation(
        self,
        conversation_id: str,
    ) -> ChatBridgeLink | None:
        """Return one stored fake bridge link for the given conversation."""
        bridge_link = await self._admin_client.get_chat_bridge_for_conversation(
            conversation_id
        )
        if bridge_link is None:
            return None
        return ChatBridgeLink.from_dict(bridge_link)

    async def sync_inbound_message(self, *, message: object) -> str:
        """Record one inbound bridge message for assertions."""
        payload = {
            "provider": str(message.provider.value),
            "external_space_id": str(message.external_space_id),
            "external_channel_id": str(message.external_channel_id),
            "external_message_id": str(message.external_message_id),
            "author_id": str(message.author_id),
            "author_name": str(message.author_name),
            "content": str(message.content),
        }
        self.inbound_messages.append(payload)
        return "bridge-inbound-message-1"


class FakeAdminClient:
    """In-memory admin client for file route testing."""

    def __init__(self) -> None:
        """Initialize fake admin data stores."""
        self._files: dict[str, dict] = {}
        self._file_contexts: dict[str, dict] = {}
        self._chat_bridge_links: dict[str, dict] = {}
        self._chat_bridge_message_links: dict[str, dict] = {}
        self._conversations = [
            {
                "id": "conversation-2",
                "title": "Conversation two",
                "created_at": "2026-03-12T10:00:00Z",
                "updated_at": "2026-03-12T10:00:00Z",
            },
            {
                "id": "conversation-1",
                "title": "Conversation one",
                "created_at": "2026-03-12T10:00:03Z",
                "updated_at": "2026-03-12T10:00:03Z",
            },
        ]
        self._profile = {
            "display_name": "Ada",
            "timezone": "Europe/Vilnius",
            "language": "lt",
            "custom_instructions": "Be concise",
        }
        self._messages = [
            {
                "id": "m1",
                "content": "message-1",
                "created_at": "2026-03-12T10:00:03Z",
                "role": "user",
                "meta": "",
                "conversation_id": "conversation-1",
            },
            {
                "id": "m2",
                "content": "message-2",
                "created_at": "2026-03-12T10:00:00Z",
                "role": "assistant",
                "meta": "",
                "conversation_id": "conversation-2",
            },
        ]
        self._observations = [
            {
                "id": "o1",
                "summary": "record-1",
                "created_at": "2026-03-12T10:00:02Z",
                "key_facts": ["fact-1"],
                "action_items": ["action-1"],
                "memory_kinds": ["preference"],
                "conversation_id": "conversation-1",
                "source_message_ids": ["m1"],
            },
            {
                "id": "o2",
                "summary": "record-2",
                "created_at": "2026-03-12T09:59:59Z",
                "key_facts": [],
                "action_items": [],
                "memory_kinds": ["action"],
                "conversation_id": "conversation-2",
                "source_message_ids": [],
            },
        ]
        self._reflections = [
            {
                "id": "r1",
                "main_point": "summary-1",
                "created_at": "2026-03-12T10:00:01Z",
                "based_on": [],
                "trust_level": "medium",
                "scope": "long_term",
                "record_ids": ["o1"],
                "record_relations": [
                    {"record_id": "o1", "relation": "supports"},
                ],
                "revalidation_status": "fresh",
                "effective_trust_level_score": 3,
                "active_support_count": 1,
                "supporting_record_count": 1,
            },
            {
                "id": "r2",
                "main_point": "summary-2",
                "created_at": "2026-03-12T09:59:58Z",
                "based_on": [],
                "trust_level": "low",
                "scope": "short_term",
                "record_ids": ["missing-record"],
                "record_relations": [
                    {
                        "record_id": "missing-record",
                        "relation": "supports",
                    },
                ],
                "revalidation_status": "orphaned",
                "effective_trust_level_score": 1,
                "active_support_count": 0,
                "supporting_record_count": 1,
            },
        ]
        self._route_diagnostics = [
            {
                "id": 1,
                "created_at": "2026-03-12T10:00:04Z",
                "session_id": "session-1",
                "conversation_id": "conversation-1",
                "message_id": "m1",
                "channel": "web",
                "initial_route": {
                    "intent": "non_memory_question",
                    "confidence": "none",
                    "role_filter": None,
                    "source": "signal",
                    "signal_groups": [],
                },
                "fallback": {
                    "attempted": True,
                    "reason": "unsupported_language_question_shape",
                    "intent": "user_recall_question",
                    "confidence": "high",
                    "role_filter": "user",
                    "source": "semantic_fallback",
                },
                "final_route": {
                    "intent": "user_recall_question",
                    "confidence": "high",
                    "role_filter": "user",
                    "source": "semantic_fallback",
                    "signal_groups": ["user_recall_signals"],
                },
                "route_mismatch": True,
                "mismatch_label": "intent_changed",
                "used_long_term_mode": False,
            }
        ]
        self._route_diagnostics_summary = {
            "totals": {
                "total_rows": 1,
                "fallback_rows": 1,
                "mismatch_rows": 1,
                "long_term_rows": 0,
            },
            "recent_hours": None,
            "mismatch_labels": [
                {"label": "intent_changed", "count": 1},
            ],
            "final_intents": [
                {"intent": "user_recall_question", "count": 1},
            ],
            "fallback_reasons": [
                {"reason": "unsupported_language_question_shape", "count": 1},
            ],
        }
        self._record_processing_diagnostics = [
            {
                "timestamp": "2026-03-15T12:00:00Z",
                "decision": "record_stored_by_llm_memory_judgment",
                "conversation_id": "conversation-1",
                "session_id": "session-1",
                "messages": ["User: hello", "Assistant: hi"],
                "source_message_ids": ["m1", "m2"],
                "message_count": 2,
            }
        ]

    async def send_operation(
        self,
        operation: str,
        parameters: dict | None = None,
    ) -> dict:
        """Route admin IPC operations to in-memory handlers."""
        operation_parameters = parameters or {}
        if operation == "list_files":
            files = sorted(
                self._files.values(),
                key=lambda item: item["created_at"],
                reverse=True,
            )
            return {"files": files}
        if operation == "create_file_metadata":
            file_id = operation_parameters["id"]
            self._files[file_id] = {
                "id": file_id,
                "original_name": operation_parameters["original_name"],
                "size": operation_parameters["size"],
                "mime_type": operation_parameters["mime_type"],
                "created_at": str(time.time()),
            }
            return {"id": file_id}
        if operation == "delete_file_metadata":
            file_id = operation_parameters["file_id"]
            existed = self._files.pop(file_id, None) is not None
            self._file_contexts.pop(file_id, None)
            return {"deleted": existed}
        if operation == "get_file_metadata":
            return {"file": self._files.get(operation_parameters["file_id"])}
        if operation == "save_file_context":
            file_id = operation_parameters["file_id"]
            self._file_contexts[file_id] = {
                "file_id": file_id,
                "file_kind": operation_parameters["file_kind"],
                "estimated_tokens": operation_parameters["estimated_tokens"],
                "processing_mode": operation_parameters["processing_mode"],
                "processing_reason": operation_parameters["processing_reason"],
                "summary_overview": operation_parameters["summary_overview"],
                "summary_structure": list(operation_parameters["summary_structure"]),
                "summary_key_points": list(operation_parameters["summary_key_points"]),
                "summary_warnings": list(operation_parameters["summary_warnings"]),
                "chunks": [dict(chunk) for chunk in operation_parameters["chunks"]],
            }
            return {
                "file_id": file_id,
                "chunk_count": len(operation_parameters["chunks"]),
            }
        if operation == "save_file_context_usage":
            file_id = operation_parameters["file_id"]
            current_context = self._file_contexts.get(file_id)
            if current_context is None:
                return {"file_id": file_id}
            current_context["last_usage"] = {
                "processing_mode": operation_parameters["processing_mode"],
                "mode_reason": operation_parameters["mode_reason"],
                "user_query": operation_parameters["user_query"],
                "selected_chunk_ids": list(operation_parameters["selected_chunk_ids"]),
                "selected_chunk_indexes": list(
                    operation_parameters["selected_chunk_indexes"]
                ),
                "used_at": str(time.time()),
            }
            return {"file_id": file_id}
        if operation == "get_file_context":
            return {
                "file_context": self._file_contexts.get(operation_parameters["file_id"])
            }
        if operation == "list_chat_bridge_links":
            links = list(self._chat_bridge_links.values())
            provider = operation_parameters.get("provider")
            conversation_id = operation_parameters.get("conversation_id")
            if provider:
                links = [item for item in links if item["provider"] == provider]
            if conversation_id:
                links = [
                    item for item in links if item["conversation_id"] == conversation_id
                ]
            return {"chat_bridge_links": links}
        if operation == "create_conversation":
            conversation_id = f"conversation-{len(self._conversations) + 1}"
            conversation = {
                "id": conversation_id,
                "title": operation_parameters.get("title", ""),
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
                "created_at": "2026-03-12T10:00:05Z",
                "updated_at": "2026-03-12T10:00:05Z",
            }
            self._conversations.insert(0, conversation)
            return {
                "conversation_id": conversation_id,
                "conversation": dict(conversation),
            }
        if operation == "list_conversations":
            limit = int(operation_parameters.get("limit", 50))
            offset = int(operation_parameters.get("offset", 0))
            return {"conversations": self._conversations[offset : offset + limit]}
        if operation == "update_conversation_title":
            conversation_id = operation_parameters["conversation_id"]
            title = operation_parameters["title"]
            for conversation in self._conversations:
                if conversation["id"] == conversation_id:
                    conversation["title"] = title
                    conversation["updated_at"] = "2026-03-12T10:00:09Z"
                    return {"conversation": dict(conversation)}
            return {"conversation": None}
        if operation == "delete_conversation":
            conversation_id = operation_parameters["conversation_id"]
            self._conversations = [
                conversation
                for conversation in self._conversations
                if conversation["id"] != conversation_id
            ]
            self._messages = [
                message
                for message in self._messages
                if message["conversation_id"] != conversation_id
            ]
            return {"ok": True}
        if operation == "create_chat_bridge_link":
            bridge_link_id = f"bridge-{len(self._chat_bridge_links) + 1}"
            link = {
                "id": bridge_link_id,
                "conversation_id": operation_parameters["conversation_id"],
                "provider": operation_parameters["provider"],
                "external_space_id": operation_parameters["external_space_id"],
                "external_channel_id": operation_parameters["external_channel_id"],
                "created_by": operation_parameters["created_by"],
                "sync_direction": operation_parameters["sync_direction"],
                "status": "active",
                "delivery_mode": operation_parameters.get("delivery_mode", "bot_only"),
                "persona_mode": operation_parameters.get("persona_mode", "neutral"),
                "persona_user_id": operation_parameters.get("persona_user_id", ""),
                "created_at": str(time.time()),
            }
            self._chat_bridge_links[bridge_link_id] = link
            return {"chat_bridge_link": link}
        if operation == "get_chat_bridge_for_conversation":
            conversation_id = operation_parameters["conversation_id"]
            for link in self._chat_bridge_links.values():
                if link["conversation_id"] == conversation_id:
                    return {"chat_bridge_link": link}
            return {"chat_bridge_link": None}
        if operation == "disable_chat_bridge_link":
            bridge_link_id = operation_parameters["bridge_link_id"]
            current_link = self._chat_bridge_links.get(bridge_link_id)
            if current_link is None:
                return {"updated": False}
            current_link["status"] = "disabled"
            return {"updated": True}
        if operation == "enable_chat_bridge_link":
            bridge_link_id = operation_parameters["bridge_link_id"]
            current_link = self._chat_bridge_links.get(bridge_link_id)
            if current_link is None:
                return {"updated": False}
            current_link["status"] = "active"
            return {"updated": True}
        if operation == "update_chat_bridge_link_settings":
            bridge_link_id = operation_parameters["bridge_link_id"]
            current_link = self._chat_bridge_links.get(bridge_link_id)
            if current_link is None:
                return {"chat_bridge_link": None}
            current_link["delivery_mode"] = operation_parameters["delivery_mode"]
            current_link["persona_mode"] = operation_parameters["persona_mode"]
            current_link["persona_user_id"] = operation_parameters["persona_user_id"]
            return {"chat_bridge_link": current_link}
        if operation == "create_chat_bridge_message_link":
            mapping_id = f"bridge-message-{len(self._chat_bridge_message_links) + 1}"
            link = {
                "id": mapping_id,
                "bridge_link_id": operation_parameters["bridge_link_id"],
                "message_id": operation_parameters["message_id"],
                "external_message_id": operation_parameters["external_message_id"],
                "direction": operation_parameters["direction"],
                "created_at": str(time.time()),
            }
            self._chat_bridge_message_links[mapping_id] = link
            return {"chat_bridge_message_link": link}
        if operation == "list_chat_bridge_message_links":
            bridge_link_id = operation_parameters["bridge_link_id"]
            direction = operation_parameters.get("direction")
            links = [
                link
                for link in self._chat_bridge_message_links.values()
                if link["bridge_link_id"] == bridge_link_id
            ]
            if direction:
                links = [link for link in links if link["direction"] == direction]
            return {"chat_bridge_message_links": links}
        if operation == "get_chat_bridge_message_link_by_message_id":
            bridge_link_id = operation_parameters["bridge_link_id"]
            message_id = operation_parameters["message_id"]
            for link in self._chat_bridge_message_links.values():
                if (
                    link["bridge_link_id"] == bridge_link_id
                    and link["message_id"] == message_id
                ):
                    return {"chat_bridge_message_link": link}
            return {"chat_bridge_message_link": None}
        raise AssertionError(f"Unexpected operation: {operation}")

    async def get_user_profile(self) -> dict:
        """Return the stored fake user profile."""
        return dict(self._profile)

    async def create_conversation(self, title: str = "") -> str:
        """Create and return one fake persisted conversation."""
        result = await self.send_operation(
            "create_conversation",
            {"title": title},
        )
        return str(result["conversation_id"])

    async def create_conversation_item(self, title: str = "") -> dict | None:
        """Create and return one fake persisted conversation row."""
        result = await self.send_operation(
            "create_conversation",
            {"title": title},
        )
        conversation = result.get("conversation")
        if not isinstance(conversation, dict):
            return None
        return conversation

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return paginated fake conversations."""
        result = await self.send_operation(
            "list_conversations",
            {"limit": limit, "offset": offset},
        )
        return result["conversations"]

    async def update_conversation_title(
        self,
        conversation_id: str,
        title: str,
    ) -> dict | None:
        """Update one fake conversation title."""
        result = await self.send_operation(
            "update_conversation_title",
            {
                "conversation_id": conversation_id,
                "title": title,
            },
        )
        return result["conversation"]

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete one fake persisted conversation."""
        await self.send_operation(
            "delete_conversation",
            {"conversation_id": conversation_id},
        )

    async def list_files(self) -> list[dict]:
        """Return stored fake file metadata."""
        return sorted(
            self._files.values(),
            key=lambda item: item["created_at"],
            reverse=True,
        )

    async def get_file_context(self, file_id: str) -> dict | None:
        """Return stored fake file context."""
        return self._file_contexts.get(file_id)

    async def list_chat_bridge_links(
        self,
        limit: int = 100,
        offset: int = 0,
        provider: str | None = None,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """Return stored fake chat bridge links."""
        del limit, offset
        links = list(self._chat_bridge_links.values())
        if provider:
            links = [item for item in links if item["provider"] == provider]
        if conversation_id:
            links = [
                item for item in links if item["conversation_id"] == conversation_id
            ]
        return links

    async def create_chat_bridge_link(
        self,
        *,
        conversation_id: str,
        provider: str,
        external_space_id: str,
        external_channel_id: str,
        created_by: str,
        sync_direction: str = "bidirectional",
        delivery_mode: str = "bot_only",
        persona_mode: str = "neutral",
        persona_user_id: str = "",
    ) -> dict:
        """Create and return one fake chat bridge link."""
        result = await self.send_operation(
            "create_chat_bridge_link",
            {
                "conversation_id": conversation_id,
                "provider": provider,
                "external_space_id": external_space_id,
                "external_channel_id": external_channel_id,
                "created_by": created_by,
                "sync_direction": sync_direction,
                "delivery_mode": delivery_mode,
                "persona_mode": persona_mode,
                "persona_user_id": persona_user_id,
            },
        )
        return result["chat_bridge_link"]

    async def get_chat_bridge_for_conversation(
        self,
        conversation_id: str,
    ) -> dict | None:
        """Return one fake chat bridge link by conversation."""
        result = await self.send_operation(
            "get_chat_bridge_for_conversation",
            {"conversation_id": conversation_id},
        )
        return result["chat_bridge_link"]

    async def get_chat_bridge_link(self, bridge_link_id: str) -> dict | None:
        """Return one fake chat bridge link by stored identifier."""
        return self._chat_bridge_links.get(bridge_link_id)

    async def disable_chat_bridge_link(self, bridge_link_id: str) -> bool:
        """Disable one fake chat bridge link."""
        result = await self.send_operation(
            "disable_chat_bridge_link",
            {"bridge_link_id": bridge_link_id},
        )
        return bool(result["updated"])

    async def enable_chat_bridge_link(self, bridge_link_id: str) -> bool:
        """Enable one fake chat bridge link."""
        result = await self.send_operation(
            "enable_chat_bridge_link",
            {"bridge_link_id": bridge_link_id},
        )
        return bool(result["updated"])

    async def update_chat_bridge_link_settings(
        self,
        *,
        bridge_link_id: str,
        delivery_mode: str,
        persona_mode: str,
        persona_user_id: str,
    ) -> dict | None:
        """Update one fake chat bridge link presentation mode."""
        result = await self.send_operation(
            "update_chat_bridge_link_settings",
            {
                "bridge_link_id": bridge_link_id,
                "delivery_mode": delivery_mode,
                "persona_mode": persona_mode,
                "persona_user_id": persona_user_id,
            },
        )
        return result["chat_bridge_link"]

    async def list_chat_bridge_message_links(
        self,
        *,
        bridge_link_id: str,
        limit: int = 100,
        offset: int = 0,
        direction: str | None = None,
    ) -> list[dict]:
        """Return stored fake bridge message mappings."""
        del limit, offset
        result = await self.send_operation(
            "list_chat_bridge_message_links",
            {
                "bridge_link_id": bridge_link_id,
                "direction": direction,
            },
        )
        return result["chat_bridge_message_links"]

    async def list_chat_settings(
        self,
        conversation_id: str,
    ) -> dict[str, str]:
        """Return empty settings for any conversation."""
        return {}

    async def list_all_chat_settings(self) -> dict[str, dict[str, str]]:
        """Return empty settings map."""
        return {}

    async def store_user_profile(self, profile: dict) -> dict:
        """Replace the stored fake user profile."""
        self._profile = dict(profile)
        return {"detail": "Profile saved"}

    async def count_memory_entries(self) -> dict:
        """Return aggregate counts for the fake memory stores."""
        return {
            "messages": len(self._messages),
            "records": len(self._observations),
            "summaries": len(self._reflections),
        }

    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """Return paginated fake chat messages."""
        messages = self._messages
        if conversation_id:
            messages = [
                message
                for message in messages
                if message["conversation_id"] == conversation_id
            ]
        return messages[offset : offset + limit]

    async def list_pending_actions(
        self,
        status: str | None = None,
        run_id: str | None = None,
    ) -> list[dict]:
        """Return no un-decided confirmations by default."""
        return []

    async def get_agent_run(self, run_id: str) -> dict | None:
        """Return no agent run by default."""
        return None

    async def list_records(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return paginated fake records."""
        return self._observations[offset : offset + limit]

    async def list_summaries_full(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return paginated full summary records."""
        return self._reflections[offset : offset + limit]

    async def list_summaries(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Return paginated summary summaries."""
        return [
            {
                "main_point": item["main_point"],
                "trust_level": "medium",
                "scope": item["scope"],
                "based_on_count": 2,
                "created_at": item["created_at"],
            }
            for item in self._reflections[offset : offset + limit]
        ]

    async def list_audit_log(
        self,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> list[dict]:
        """Return an empty audit log for tests that do not need one."""
        return []

    async def list_memory_route_diagnostics(
        self,
        limit: int = 50,
        offset: int = 0,
        final_intent: str | None = None,
        mismatch_only: bool = False,
        fallback_only: bool = False,
    ) -> list[dict]:
        """Return paginated fake route diagnostics."""
        diagnostics = self._route_diagnostics
        if final_intent:
            diagnostics = [
                item
                for item in diagnostics
                if item["final_route"]["intent"] == final_intent
            ]
        if mismatch_only:
            diagnostics = [item for item in diagnostics if item["route_mismatch"]]
        if fallback_only:
            diagnostics = [
                item for item in diagnostics if item["fallback"]["attempted"]
            ]
        return diagnostics[offset : offset + limit]

    async def get_memory_route_diagnostics_summary(
        self,
        recent_hours: int | None = None,
    ) -> dict:
        """Return fake aggregated route-diagnostics summary."""
        return {
            **self._route_diagnostics_summary,
            "recent_hours": recent_hours,
        }

    async def get_memory_quality_report(self) -> dict:
        """Return a fake memory quality report."""
        return {
            "quality_score": 87,
            "records": {
                "total": 2,
                "with_provenance": 1,
            },
            "summaries": {
                "status_breakdown": {
                    "fresh": 1,
                    "orphaned": 1,
                }
            },
        }

    async def get_memory_timeline(
        self,
        entry_type: str = "",
        sort: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Return a merged fake memory timeline."""
        entries = [
            {
                "id": "m1",
                "type": "message",
                "created_at": "2026-03-12T10:00:03Z",
            },
            {
                "id": "o1",
                "type": "record",
                "created_at": "2026-03-12T10:00:02Z",
            },
            {
                "id": "r1",
                "type": "summary",
                "created_at": "2026-03-12T10:00:01Z",
            },
            {
                "id": "m2",
                "type": "message",
                "created_at": "2026-03-12T10:00:00Z",
            },
            {
                "id": "o2",
                "type": "record",
                "created_at": "2026-03-12T09:59:59Z",
            },
            {
                "id": "r2",
                "type": "summary",
                "created_at": "2026-03-12T09:59:58Z",
            },
        ]
        if entry_type:
            entries = [entry for entry in entries if entry["type"] == entry_type]
        reverse = sort != "asc"
        entries.sort(key=lambda entry: entry["created_at"], reverse=reverse)
        return {
            "entries": entries[offset : offset + limit],
            "total_count": len(entries),
        }

    async def list_record_processing_diagnostics(
        self,
        limit: int = 100,
        decision: str | None = None,
    ) -> list[dict]:
        """Return fake record-processing diagnostics."""
        entries = self._record_processing_diagnostics
        if decision:
            entries = [entry for entry in entries if entry["decision"] == decision]
        return entries[:limit]


# ===========================================================================
# Task 12.4: LLM config CRUD integration tests
# ===========================================================================


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
        assert create_resp.status_code == 200, create_resp.text
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

        update_resp = client.put(
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
        """PUT on nonexistent config returns 404."""
        client, csrf, _ = self._setup()
        resp = client.put(
            "/admin/llm/configs/nonexistent-id",
            json={"model": "x"},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 404

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
        assert delete_resp.status_code == 200
        assert (
            delete_resp.json()["detail"]
            == "Configuration deleted and runtime reloaded."
        )
        assert delete_resp.json()["reload"]["success"] is True
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
        assert create_resp.status_code == 200
        config_id = create_resp.json()["id"]

        # Activate
        client.post(f"/admin/llm/configs/{config_id}/activate", headers=headers)

        # Update model
        client.put(
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
        assert ok_resp.status_code == 200, ok_resp.text

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
        """POST /admin/chat-bridges/{id}/settings should store bridge send mode."""
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

        response = client.post(
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


class TestRefreshRouteActiveStack:
    """Integration tests for refresh behavior in the active FastAPI stack."""

    def test_refresh_succeeds_with_refresh_cookie_only(self) -> None:
        """Expired or missing access token should not be required for refresh."""
        app, _, _, signing_client, _ = _build_test_app()
        client = TestClient(app)

        refresh_token = app.state.session_manager.async_issue_refresh_token(
            signing_client,
        )
        token_value = _awaitable_result(refresh_token)
        client.cookies.set("refresh_token", token_value)

        resp = client.post("/refresh")
        assert resp.status_code == 200, resp.text
        assert "access_token" in resp.cookies
        assert "refresh_token" in resp.cookies

    def test_refresh_rejects_access_token_in_refresh_cookie(self) -> None:
        """Refresh endpoint must reject cookies that carry an access token."""
        app, access_token, _, _, _ = _build_test_app()
        client = TestClient(app)
        client.cookies.set("refresh_token", access_token)

        resp = client.post("/refresh")
        assert resp.status_code == 401


def _awaitable_result(awaitable: object) -> object:
    """Run a one-shot awaitable in tests without extra fixtures."""
    import asyncio

    return asyncio.run(awaitable)


class TestFileReplacement:
    """Integration tests for replacement semantics in file uploads."""

    def test_upload_replaces_existing_file_with_same_name(self) -> None:
        """Repeated uploads should keep only the latest file metadata and disk file."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                first_resp = client.post(
                    "/files/upload",
                    files={"file": ("records.txt", b"first version", "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert first_resp.status_code == 200, first_resp.text
                first_id = first_resp.json()["id"]

                second_resp = client.post(
                    "/files/upload",
                    files={"file": ("records.txt", b"second version", "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert second_resp.status_code == 200, second_resp.text
                second_id = second_resp.json()["id"]

                files_resp = client.get("/files")
                assert files_resp.status_code == 200
                listed_files = files_resp.json()["files"]
                assert len(listed_files) == 1
                assert listed_files[0]["id"] == second_id
                assert listed_files[0]["original_name"] == "records.txt"
                assert not (Path(tmp_dir) / f"{first_id}.txt").exists()
                replacement_file = Path(tmp_dir) / f"{second_id}.txt"
                assert replacement_file.read_text() == "second version"
            finally:
                files_module.FILES_DIR = original_files_dir

    def test_chat_resolves_attached_files_from_file_ids(self) -> None:
        """Chat should resolve attached file identifiers to readable filenames."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed orchestrator response for file-id chat tests."""
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

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("context.txt", b"context body", "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 200, upload_resp.text
                file_id = upload_resp.json()["id"]

                chat_resp = client.post(
                    "/chat",
                    json={"message": "use file", "file_ids": [file_id]},
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert chat_resp.json()["stream_started"] is True
                assert len(sent_requests) == 1
                assert file_id in admin._file_contexts
                assert sent_requests[0].attached_files == ["context.txt [inline]"]
                assert "--- File: context.txt ---" in sent_requests[0].message
            finally:
                files_module.FILES_DIR = original_files_dir

    def test_chat_summarizes_large_attached_text_files(self) -> None:
        """Large text files should summarize instead of raw full inline injection."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for file summary tests."""
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

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "# Rollout Plan",
                        "Prepare database backup before deploy.",
                        "Update status page before maintenance window starts.",
                        "Verify health checks after rollout.",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("rollout.md", large_content, "text/markdown")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 200, upload_resp.text
                file_id = upload_resp.json()["id"]

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "Summarize the rollout file.",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert chat_resp.json()["stream_started"] is True
                assert len(sent_requests) == 1
                assert file_id in admin._file_contexts
                assert sent_requests[0].attached_files == ["rollout.md [summarized]"]
                assert "File summary:" in sent_requests[0].message
                assert (
                    "This file was summarized before prompt injection"
                    in sent_requests[0].message
                )
                assert "--- File: rollout.md ---" not in sent_requests[0].message
            finally:
                files_module.FILES_DIR = original_files_dir

    def test_chat_reuses_stored_large_file_context_without_raw_disk_read(self) -> None:
        """Large-file chat should still work after raw bytes disappear."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for stored-file reuse tests."""
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

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "# Incident Review",
                        "Root cause was a stale deployment artifact.",
                        "Recovery required a forced cache refresh.",
                        "Follow-up should document the rollback process.",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("incident.md", large_content, "text/markdown")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 200, upload_resp.text
                file_id = upload_resp.json()["id"]
                stored_path = Path(tmp_dir) / f"{file_id}.md"
                stored_path.unlink()

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "What exact lines mention rollback details?",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert len(sent_requests) == 1
                assert sent_requests[0].attached_files == [
                    "incident.md [chunked_recall]"
                ]
                assert "Relevant file chunks:" in sent_requests[0].message
                assert "Follow-up should document the rollback process." in (
                    sent_requests[0].message
                )
            finally:
                files_module.FILES_DIR = original_files_dir

    def test_chat_falls_back_to_metadata_block_when_small_raw_file_is_missing(
        self,
    ) -> None:
        """Small stored files should stay visible if raw bytes disappear."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for missing raw-file tests."""
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

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                upload_resp = client.post(
                    "/files/upload",
                    files={
                        "file": (
                            "note.txt",
                            b"Short deployment note.",
                            "text/plain",
                        )
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 200, upload_resp.text
                file_id = upload_resp.json()["id"]
                stored_path = Path(tmp_dir) / f"{file_id}.txt"
                stored_path.unlink()

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "Use the attached file.",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert len(sent_requests) == 1
                assert sent_requests[0].attached_files == ["note.txt [metadata_only]"]
                assert "Attached file: note.txt" in sent_requests[0].message
                assert "Mode: metadata_only" in sent_requests[0].message
                assert "raw file bytes are no longer available on disk" in (
                    sent_requests[0].message
                )
            finally:
                files_module.FILES_DIR = original_files_dir

    def test_admin_memory_file_context_view_returns_stored_context(self) -> None:
        """Admin memory route should expose stored summary and chunks for one file."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "# Runbook",
                        "Prepare rollback plan.",
                        "Restart edge workers after deploy.",
                        "Document health checks in the incident note.",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("runbook.md", large_content, "text/markdown")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 200, upload_resp.text
                file_id = upload_resp.json()["id"]

                files_resp = client.get("/admin/memory/files")
                assert files_resp.status_code == 200, files_resp.text
                assert files_resp.json()["files"][0]["id"] == file_id

                context_resp = client.get(f"/admin/memory/files/{file_id}/context")
                assert context_resp.status_code == 200, context_resp.text
                payload = context_resp.json()["file_context"]
                assert payload is not None
                assert payload["file_id"] == file_id
                assert payload["processing_mode"] == "summarized"
                assert payload["processing_reason"]
                assert payload["summary_overview"].startswith("runbook.md is a")
                assert payload["chunks"]
                assert payload["prompt_blocks"]
                assert any(
                    "File summary:" in block for block in payload["prompt_blocks"]
                )
            finally:
                files_module.FILES_DIR = original_files_dir

    def test_admin_memory_file_context_view_shows_last_prompt_usage(self) -> None:
        """Admin memory file context should include the latest prompt usage snapshot."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for prompt-usage memory tests."""
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

        with TemporaryDirectory() as tmp_dir:
            import backend.processes.fastapi_app.routes.files as files_module

            original_files_dir = files_module.FILES_DIR
            files_module.FILES_DIR = Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "feature_flag=true",
                        "rollout_strategy=blue-green",
                        "healthcheck_path=/health",
                        "retry_limit=5",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("deploy.conf", large_content, "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 200, upload_resp.text
                file_id = upload_resp.json()["id"]

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "Which lines show the rollout strategy exactly?",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text

                context_resp = client.get(f"/admin/memory/files/{file_id}/context")
                assert context_resp.status_code == 200, context_resp.text
                payload = context_resp.json()["file_context"]
                assert payload is not None
                assert payload["last_usage"] is not None
                assert payload["last_usage"]["processing_mode"] == "chunked_recall"
                assert payload["last_usage"]["mode_reason"]
                assert payload["last_usage"]["user_query"] == (
                    "Which lines show the rollout strategy exactly?"
                )
                assert payload["last_usage"]["selected_chunk_indexes"]
                assert payload["prompt_blocks"]
                assert any(
                    "Relevant file chunks:" in block
                    for block in payload["prompt_blocks"]
                )
            finally:
                files_module.FILES_DIR = original_files_dir

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

        assert response.status_code == 200, response.text
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

        assert response.status_code == 200, response.text
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
        assert create_response.status_code == 200, create_response.text
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
        assert create_response.status_code == 200, create_response.text
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

    def test_chat_returns_usage_stats_from_orchestrator(self) -> None:
        """Chat should pass usage statistics through to the HTTP response."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed response that includes usage statistics."""
            return MagicMock(
                text="ready",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats={
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
                },
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
            json={"message": "show stats"},
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200, response.text
        assert response.json()["stats"] == {
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
            "memory_zones": None,
            "model": "qwen3.5:35b",
            "iterations": 2,
        }


class TestProfileContract:
    """Integration tests for the user profile route contract."""

    def test_profile_returns_raw_profile_object(self) -> None:
        """GET /admin/profile should return the profile object directly."""
        app, token, sm, sc, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(app, token, sm, sc)

        resp = client.get("/admin/profile")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "display_name": "Ada",
            "timezone": "Europe/Vilnius",
            "language": "lt",
            "custom_instructions": "Be concise",
        }

    def test_profile_save_persists_updated_fields(self) -> None:
        """POST /admin/profile should persist the raw profile payload."""
        app, token, sm, sc, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        save_resp = client.post(
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
            "first_name": "",
            "last_name": "",
            "display_name": "Grace",
            "assistant_name": "",
            "timezone": "UTC",
            "language": "en",
            "custom_instructions": "Prefer terse answers",
        }


class TestSummariesContract:
    """Integration tests for the active summaries route contract."""


class TestMemoryTimeline:
    """Integration tests for merged memory timeline pagination."""

    def test_timeline_applies_offset_after_merging_streams(self) -> None:
        """Timeline pagination must apply after merging all memory streams."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/timeline?limit=2&offset=1&sort=desc")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        entries = body["entries"]
        assert [entry["id"] for entry in entries] == ["o1", "r1"]
        assert body["total_count"] == 6

    def test_quality_route_returns_memory_health_report(self) -> None:
        """Memory quality route should expose diagnostic quality metrics."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/quality")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "quality_score" in body
        assert body["records"]["total"] == 2
        assert body["summaries"]["status_breakdown"]["orphaned"] == 1
        assert body["records"]["with_provenance"] == 1

    def test_messages_route_supports_conversation_filter(self) -> None:
        """Messages route should return raw entries for one conversation."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/messages?conversation_id=conversation-1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "messages": [
                {
                    "id": "m1",
                    "content": "message-1",
                    "created_at": "2026-03-12T10:00:03Z",
                    "role": "user",
                    "meta": "",
                    "conversation_id": "conversation-1",
                }
            ]
        }

    def test_chat_conversations_route_returns_server_backed_list(self) -> None:
        """Return stored conversations from the database chat list route."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/chat/conversations")

        assert resp.status_code == 200, resp.text
        default_settings = {"show_thinking": False}
        assert resp.json() == [
            {
                "id": "conversation-2",
                "title": "Conversation two",
                "bridge_provider": None,
                "settings": default_settings,
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
                "created_at": "2026-03-12T10:00:00Z",
                "updated_at": "2026-03-12T10:00:00Z",
            },
            {
                "id": "conversation-1",
                "title": "Conversation one",
                "bridge_provider": None,
                "settings": default_settings,
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
                "created_at": "2026-03-12T10:00:03Z",
                "updated_at": "2026-03-12T10:00:03Z",
            },
        ]

    def test_chat_conversation_messages_route_returns_ordered_history(self) -> None:
        """Return one persisted conversation history in ascending time order."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/chat/conversations/conversation-1/messages")

        assert resp.status_code == 200, resp.text
        assert resp.json() == [
            {
                "id": "m1",
                "content": "message-1",
                "created_at": "2026-03-12T10:00:03Z",
                "role": "user",
                "meta": "",
                "conversation_id": "conversation-1",
                "structured_content": None,
                "used_tools": [],
                "pending_confirmations": [],
            }
        ]

    def test_chat_create_conversation_route_persists_server_side_chat(self) -> None:
        """Chat create conversation route should create one persisted conversation."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, csrf_token = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.post(
            "/chat/conversations",
            headers={"X-CSRF-Token": csrf_token},
            json={"title": "Server chat"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"conversation_id": "conversation-3"}
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_kwargs = (
            app.state.orchestrator_client.publish_conversation_event.await_args.kwargs
        )
        assert publish_kwargs["conversation_id"] == "conversation-3"
        assert publish_kwargs["event_type"].value == "conversation_created"
        assert publish_kwargs["data"]["conversation_id"] == "conversation-3"
        assert publish_kwargs["data"]["title"] == "Server chat"
        assert publish_kwargs["data"]["bridge_provider"] is None

    def test_chat_update_conversation_route_renames_server_side_chat(self) -> None:
        """Chat update conversation route should rename one persisted chat."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, csrf_token = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.put(
            "/chat/conversations/conversation-1",
            headers={"X-CSRF-Token": csrf_token},
            json={"title": "Renamed chat"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "id": "conversation-1",
            "title": "Renamed chat",
            "bridge_provider": None,
            "settings": {"show_thinking": False},
            "processing_status": "idle",
            "processing_stage": "",
            "processing_started_at": None,
            "created_at": "2026-03-12T10:00:03Z",
            "updated_at": "2026-03-12T10:00:09Z",
        }
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_kwargs = (
            app.state.orchestrator_client.publish_conversation_event.await_args.kwargs
        )
        assert publish_kwargs["conversation_id"] == "conversation-1"
        assert publish_kwargs["event_type"].value == "conversation_updated"
        assert publish_kwargs["data"]["title"] == "Renamed chat"
        assert publish_kwargs["data"]["bridge_provider"] is None

    def test_chat_delete_conversation_route_removes_server_side_chat(self) -> None:
        """Chat delete conversation route should remove one persisted chat."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, csrf_token = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.delete(
            "/chat/conversations/conversation-1",
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}
        app.state.orchestrator_client.publish_conversation_event.assert_awaited_once()
        publish_kwargs = (
            app.state.orchestrator_client.publish_conversation_event.await_args.kwargs
        )
        assert publish_kwargs["conversation_id"] == "conversation-1"
        assert publish_kwargs["event_type"].value == "conversation_deleted"
        assert publish_kwargs["data"]["conversation_id"] == "conversation-1"

    def test_observations_route_returns_full_records(self) -> None:
        """Records route should expose provenance-rich record data."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/records?limit=1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "records": [
                {
                    "id": "o1",
                    "summary": "record-1",
                    "created_at": "2026-03-12T10:00:02Z",
                    "key_facts": ["fact-1"],
                    "action_items": ["action-1"],
                    "memory_kinds": ["preference"],
                    "conversation_id": "conversation-1",
                    "source_message_ids": ["m1"],
                }
            ]
        }


    def test_route_diagnostics_route_returns_structured_records(self) -> None:
        """Route diagnostics route should expose stored routing decisions."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get(
            "/admin/memory/route-diagnostics"
            "?limit=1&mismatch_only=true&final_intent=user_recall_question"
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "diagnostics": [
                {
                    "id": 1,
                    "created_at": "2026-03-12T10:00:04Z",
                    "session_id": "session-1",
                    "conversation_id": "conversation-1",
                    "message_id": "m1",
                    "channel": "web",
                    "initial_route": {
                        "intent": "non_memory_question",
                        "confidence": "none",
                        "role_filter": None,
                        "source": "signal",
                        "signal_groups": [],
                    },
                    "fallback": {
                        "attempted": True,
                        "reason": "unsupported_language_question_shape",
                        "intent": "user_recall_question",
                        "confidence": "high",
                        "role_filter": "user",
                        "source": "semantic_fallback",
                    },
                    "final_route": {
                        "intent": "user_recall_question",
                        "confidence": "high",
                        "role_filter": "user",
                        "source": "semantic_fallback",
                        "signal_groups": ["user_recall_signals"],
                    },
                    "route_mismatch": True,
                    "mismatch_label": "intent_changed",
                    "used_long_term_mode": False,
                }
            ]
        }

    def test_route_diagnostics_summary_route_returns_aggregates(self) -> None:
        """Route diagnostics summary route should expose aggregate counts."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/route-diagnostics/summary")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "totals": {
                "total_rows": 1,
                "fallback_rows": 1,
                "mismatch_rows": 1,
                "long_term_rows": 0,
            },
            "recent_hours": None,
            "mismatch_labels": [
                {"label": "intent_changed", "count": 1},
            ],
            "final_intents": [
                {"intent": "user_recall_question", "count": 1},
            ],
            "fallback_reasons": [
                {
                    "reason": "unsupported_language_question_shape",
                    "count": 1,
                }
            ],
        }

    def test_route_diagnostics_summary_route_accepts_recent_hours(self) -> None:
        """Route diagnostics summary route should pass the recent-hours filter."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/route-diagnostics/summary?recent_hours=24")

        assert resp.status_code == 200, resp.text
        assert resp.json()["recent_hours"] == 24

    def test_dialog_recorder_trace_route_returns_record_entries(self) -> None:
        """Recorder trace route exposes recent record-processing entries."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        app.state.admin_client = FakeAdminClient()
        client, _ = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        resp = client.get("/admin/memory/dialog-recorder-trace?limit=1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "entries": [
                {
                    "timestamp": "2026-03-15T12:00:00Z",
                    "decision": "record_stored_by_llm_memory_judgment",
                    "conversation_id": "conversation-1",
                    "session_id": "session-1",
                    "messages": ["User: hello", "Assistant: hi"],
                    "source_message_ids": ["m1", "m2"],
                    "message_count": 2,
                }
            ]
        }


# ===========================================================================
# Task 12.5: Security event audit logging integration tests
# ===========================================================================


class TestSecurityAuditLogging:
    """Integration tests for audit log entries on security events."""

    def test_failed_login_generates_audit_entry(self) -> None:
        """POST /auth/login with wrong password -> audit_log has auth_failure."""
        app, _token, _session_manager, _signing_client, audit_log = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/auth/login",
            json={"password": "wrong-password"},
        )
        assert resp.status_code == 401

        auth_failures = [e for e in audit_log if e["event_type"] == "auth_failure"]
        assert len(auth_failures) >= 1
        assert auth_failures[0]["tool_name"] == "login"

    def test_successful_login_generates_audit_entry(self) -> None:
        """POST /auth/login with correct password -> audit_log has auth_success."""
        app, _token, _session_manager, _signing_client, audit_log = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/auth/login",
            json={"password": "correct-password"},
        )
        assert resp.status_code == 200

        auth_successes = [e for e in audit_log if e["event_type"] == "auth_success"]
        assert len(auth_successes) >= 1
        assert auth_successes[0]["tool_name"] == "login"

    def test_csrf_rejection_generates_audit_entry(self) -> None:
        """POST /chat without CSRF token -> audit_log has csrf_rejected."""
        app, token, _session_manager, _signing_client, audit_log = _build_test_app()
        client = _authed_client(app, token)

        # First get a CSRF token so the session has one registered,
        # but then send a request WITHOUT it
        client.get("/csrf-token")

        resp = client.post(
            "/chat",
            json={"message": "hello"},
            # No X-CSRF-Token header -> should be rejected
        )
        assert resp.status_code == 403

        csrf_entries = [e for e in audit_log if e["event_type"] == "csrf_rejected"]
        assert len(csrf_entries) >= 1

    def test_rate_limit_generates_audit_entry(self) -> None:
        """Exhaust rate limit -> audit_log has rate_limited entry."""
        app, token, _session_manager, _signing_client, audit_log = _build_test_app()
        limiter = app.state.rate_limiter

        # Exhaust the rate limit for the "health" endpoint (GET, no CSRF needed)
        for _ in range(60):
            limiter.record("health")

        client = _authed_client(app, token)
        resp = client.get("/health")
        assert resp.status_code == 429

        rate_entries = [e for e in audit_log if e["event_type"] == "rate_limited"]
        assert len(rate_entries) >= 1

    def test_multiple_security_events_accumulate(self) -> None:
        """Multiple security events all appear in the audit log."""
        app, token, _session_manager, _signing_client, audit_log = _build_test_app()
        app.state.credential_client = FakeCredentialClient()

        # 1. Failed login
        unauthenticated_client = TestClient(app)
        unauthenticated_client.post(
            "/auth/login",
            json={"password": "wrong-password"},
        )

        # 2. CSRF rejection (authed but no CSRF)
        client = _authed_client(app, token)
        client.get("/csrf-token")  # Register CSRF for session
        client.post("/chat", json={"message": "hi"})  # No CSRF header

        # 3. Rate limit
        limiter = app.state.rate_limiter
        for _ in range(60):
            limiter.record("health")
        TestClient(app).get("/health")

        event_types = {e["event_type"] for e in audit_log}
        assert "auth_failure" in event_types
        assert "csrf_rejected" in event_types
        assert "rate_limited" in event_types

    def test_auth_failure_does_not_leak_password(self) -> None:
        """Verify audit entries never contain the submitted password."""
        app, _, _, _, audit_log = _build_test_app()
        client = TestClient(app)

        secret_password = "super-secret-p@ssw0rd-12345"
        client.post(
            "/auth/login",
            json={"password": secret_password},
        )

        for entry in audit_log:
            for value in entry.values():
                if isinstance(value, str):
                    assert secret_password not in value, (
                        f"Password leaked into audit entry: {entry}"
                    )


class TestAuthRateLimitHeaders:
    """Integration tests for propagated retry-after headers."""

    def test_login_uses_retry_after_from_signing_proxy(self) -> None:
        """POST /auth/login should preserve dynamic retry-after from signing proxy."""
        app, _, _, _, _ = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/auth/login",
            json={"password": "rate-limited-password"},
        )

        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "120"

    def test_reauth_uses_retry_after_from_signing_proxy(self) -> None:
        """POST /admin/reauth should preserve dynamic retry-after from signing proxy."""
        app, token, sm, sc, _ = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)

        resp = client.post(
            "/admin/reauth",
            json={"password": "rate-limited-password"},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "120"
