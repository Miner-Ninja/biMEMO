"""In-memory fakes and app factory shared by the LLM-config route tests."""

import secrets
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
from backend.middleware.csrf import CsrfMiddleware
from backend.middleware.jwt_auth import JwtAuthMiddleware
from backend.middleware.rate_limit import RateLimitMiddleware
from backend.processes.fastapi_app.deps import _csrf_tokens
from backend.processes.fastapi_app.problem import register_problem_handlers
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
        from backend.ipc.errors import PASSWORD_ALREADY_EXISTS_CODE
        from backend.processes.fastapi_app.base_client import DBProxyHTTPError

        raise DBProxyHTTPError(
            status_code=500,
            detail="password_already_exists",
            code=PASSWORD_ALREADY_EXISTS_CODE,
        )


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
    register_problem_handlers(app)

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

    # Route that raises an unhandled exception: exercises the production
    # 500 problem+json handler registered above in the test app.
    @app.get("/test/unhandled-error")
    def _unhandled_error() -> None:
        """Raise an unhandled exception to exercise the 500 handler."""
        raise RuntimeError("unhandled test error")

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
                "finalization_id": "final-1",
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
        """Return the stored fake user profile, wrapped as the db-proxy handler does."""
        return {"profile": dict(self._profile)}

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

    async def list_message_page(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict:
        """Return one newest-first cursor page of fake chat messages."""
        del cursor
        messages = self._messages
        if conversation_id:
            messages = [
                message
                for message in messages
                if message["conversation_id"] == conversation_id
            ]
        ordered = sorted(
            messages,
            key=lambda message: str(message.get("created_at", "")),
            reverse=True,
        )
        return {
            "messages": ordered[:limit],
            "next_cursor": None,
            "has_more": False,
        }

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


def _awaitable_result(awaitable: object) -> object:
    """Run a one-shot awaitable in tests without extra fixtures."""
    import asyncio

    return asyncio.run(awaitable)
