"""Tests for WebSocket chat realtime helpers."""

import asyncio
from types import SimpleNamespace

from backend.channels.jwt_auth_models import TokenClaims
from backend.channels.realtime import RealtimeConnectionManager
from backend.ipc.models import StreamEvent, StreamEventType
from backend.processes.fastapi_app.routes.chat import chat_router
from backend.processes.fastapi_app.routes.chat_websocket import (
    authenticate_websocket,
    build_websocket_payload,
    disconnect_websocket_session,
    register_websocket_session,
)


class _FakeOrchestratorClient:
    """Minimal orchestrator client stand-in for queue lifecycle tests."""

    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue] = {}
        self.conversation_ids: dict[str, str] = {}

    def register_session_queue(
        self,
        session_id: str,
        queue: asyncio.Queue,
        conversation_id: str = "",
    ) -> None:
        """Record a registered stream queue."""
        self.queues[session_id] = queue
        self.conversation_ids[session_id] = conversation_id

    def unregister_session_queue(
        self,
        session_id: str,
        queue: asyncio.Queue | None = None,
    ) -> None:
        """Remove a registered stream queue."""
        if queue is not None and self.queues.get(session_id) is not queue:
            return
        self.queues.pop(session_id, None)
        self.conversation_ids.pop(session_id, None)


class _FakeSessionManager:
    """Session manager stand-in that records async token validation calls."""

    def __init__(self, claims: TokenClaims | None) -> None:
        self.claims = claims
        self.validated_tokens: list[str] = []

    async def async_validate_token(
        self,
        token: str,
        signing_client: object,
        audit_fn: object = None,
    ) -> TokenClaims | None:
        """Return configured claims while recording the token."""
        self.validated_tokens.append(token)
        return self.claims


def test_build_websocket_payload_matches_realtime_envelope() -> None:
    """WebSocket payloads use the same event envelope as browser realtime events."""
    event = StreamEvent(
        request_id="request-1",
        event_type=StreamEventType.FINAL,
        data={"structured_content": {"answer": "Done"}},
    )

    assert build_websocket_payload(event) == {
        "type": StreamEventType.FINAL,
        "request_id": "request-1",
        "data": {"structured_content": {"answer": "Done"}},
    }


def test_chat_router_does_not_expose_stream_endpoint() -> None:
    """The old HTTP stream route is removed."""
    routes = {
        (next(iter(route.methods)), route.path)
        for route in chat_router.routes
        if hasattr(route, "methods")
    }
    assert ("GET", "/stream") not in routes


def test_register_and_disconnect_websocket_session() -> None:
    """WebSocket sessions share queue registration and cleanup with realtime."""
    orchestrator_client = _FakeOrchestratorClient()
    realtime_manager = RealtimeConnectionManager()

    queue, connection = register_websocket_session(
        session_id="session-1",
        session_exp=9_999_999_999,
        conversation_id="conversation-1",
        orchestrator_client=orchestrator_client,
        realtime_manager=realtime_manager,
        session_manager=SimpleNamespace(
            register_session=lambda _jti, _exp: True,
            unregister_session=lambda _jti: True,
        ),
    )

    assert orchestrator_client.queues["session-1"] is queue
    assert orchestrator_client.conversation_ids["session-1"] == "conversation-1"
    assert realtime_manager.is_valid("session-1") is True

    disconnect_websocket_session(
        session_id="session-1",
        queue=queue,
        connection=connection,
        orchestrator_client=orchestrator_client,
        realtime_manager=realtime_manager,
    )

    assert "session-1" not in orchestrator_client.queues
    assert realtime_manager.is_valid("session-1") is False


def test_authenticate_websocket_uses_existing_access_cookie() -> None:
    """WebSocket auth validates the existing access-token cookie."""
    claims = TokenClaims(
        jti="session-1",
        iat=1,
        exp=9_999_999_999,
        generation=0,
        token_type="access",
    )
    session_manager = _FakeSessionManager(claims)
    websocket = SimpleNamespace(cookies={"access_token": "token-1"})

    result = asyncio.run(
        authenticate_websocket(
            websocket=websocket,
            session_manager=session_manager,
            signing_client=SimpleNamespace(),
            audit_fn=None,
        )
    )

    assert result == claims
    assert session_manager.validated_tokens == ["token-1"]


def test_authenticate_websocket_rejects_missing_cookie() -> None:
    """WebSocket auth rejects clients without the existing access cookie."""
    session_manager = _FakeSessionManager(None)
    websocket = SimpleNamespace(cookies={})

    result = asyncio.run(
        authenticate_websocket(
            websocket=websocket,
            session_manager=session_manager,
            signing_client=SimpleNamespace(),
            audit_fn=None,
        )
    )

    assert result is None
    assert session_manager.validated_tokens == []
