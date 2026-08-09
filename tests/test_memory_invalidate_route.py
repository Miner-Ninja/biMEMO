"""Tests for user-requested memory invalidation (memory-page R4).

Covers the web route guard (auth + single-use re-auth), the forwarding
to the orchestrator over IPC, and the allow-list invariant: the web
socket gains no graph write operation.
"""

from typing import Any

import pytest

from backend.channels.endpoints import ReauthManager
from backend.ipc.models import (
    MemoryInvalidateRequest,
    MemoryInvalidateResponse,
)
from backend.processes.db_proxy.registry import SOCKET_B_OPERATIONS
from backend.processes.fastapi_app.routes.admin_memory import (
    invalidate_memory_assertion,
)
from backend.processes.orchestrator.factories import make_memory_invalidate_fn
from backend.processes.orchestrator.ipc_server import FRONTEND_TYPE_MAP


class _FakeAuthState:
    """Minimal request.state carrying authenticated claims."""

    def __init__(self) -> None:
        self._state = {"claims": {"sub": "admin"}}


class _FakeAuthRequest:
    """Minimal Request stand-in for direct route-function calls."""

    def __init__(self) -> None:
        self.state = _FakeAuthState()
        self.headers: dict[str, str] = {}


class _FakeOrchestratorClient:
    """Records memory_invalidate calls and returns a canned response."""

    def __init__(self, invalidated: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self._invalidated = invalidated

    async def memory_invalidate(
        self,
        *,
        assertion_id: str,
        reason: str,
        timeout: float = 10.0,
    ) -> MemoryInvalidateResponse:
        self.calls.append({"assertion_id": assertion_id, "reason": reason})
        return MemoryInvalidateResponse(
            request_id="r1",
            invalidated=self._invalidated,
        )


class TestInvalidateRoute:
    """R4 — re-auth gate and orchestrator forwarding."""

    @pytest.mark.asyncio
    async def test_missing_reauth_rejected(self) -> None:
        """S4.2 — no token -> 403, orchestrator untouched."""
        from fastapi import HTTPException

        orch = _FakeOrchestratorClient()
        with pytest.raises(HTTPException) as exc:
            await invalidate_memory_assertion(
                "a1", "forget", _FakeAuthRequest(), None, ReauthManager(), orch
            )
        assert exc.value.status_code == 403
        assert orch.calls == []

    @pytest.mark.asyncio
    async def test_bogus_reauth_rejected(self) -> None:
        """S4.2 — invalid token -> 403, orchestrator untouched."""
        from fastapi import HTTPException

        orch = _FakeOrchestratorClient()
        with pytest.raises(HTTPException) as exc:
            await invalidate_memory_assertion(
                "a1", "forget", _FakeAuthRequest(), "bogus", ReauthManager(), orch
            )
        assert exc.value.status_code == 403
        assert orch.calls == []

    @pytest.mark.asyncio
    async def test_bad_reason_rejected(self) -> None:
        """R4 — unknown reason -> 400 even with a valid token."""
        from fastapi import HTTPException

        mgr = ReauthManager()
        orch = _FakeOrchestratorClient()
        with pytest.raises(HTTPException) as exc:
            await invalidate_memory_assertion(
                "a1", "purge", _FakeAuthRequest(), mgr.issue_token(), mgr, orch
            )
        assert exc.value.status_code == 400
        assert orch.calls == []

    @pytest.mark.asyncio
    async def test_valid_flow_forwards_to_orchestrator(self) -> None:
        """S4.1 — valid token -> orchestrator called, result returned."""
        mgr = ReauthManager()
        orch = _FakeOrchestratorClient(invalidated=True)
        result = await invalidate_memory_assertion(
            "a1", "incorrect", _FakeAuthRequest(), mgr.issue_token(), mgr, orch
        )
        assert result == {"invalidated": True}
        assert orch.calls == [{"assertion_id": "a1", "reason": "incorrect"}]

    @pytest.mark.asyncio
    async def test_reauth_token_is_single_use(self) -> None:
        """S4.2 — a consumed token does not authorize a second call."""
        from fastapi import HTTPException

        mgr = ReauthManager()
        orch = _FakeOrchestratorClient()
        token = mgr.issue_token()
        await invalidate_memory_assertion(
            "a1", "forget", _FakeAuthRequest(), token, mgr, orch
        )
        with pytest.raises(HTTPException) as exc:
            await invalidate_memory_assertion(
                "a2", "forget", _FakeAuthRequest(), token, mgr, orch
            )
        assert exc.value.status_code == 403
        assert len(orch.calls) == 1

    @pytest.mark.asyncio
    async def test_unknown_assertion_reports_failure(self) -> None:
        """S4.4 — orchestrator reports invalidated=false, route passes it on."""
        mgr = ReauthManager()
        orch = _FakeOrchestratorClient(invalidated=False)
        result = await invalidate_memory_assertion(
            "missing", "forget", _FakeAuthRequest(), mgr.issue_token(), mgr, orch
        )
        assert result == {"invalidated": False}


class TestOrchestratorSide:
    """R4 — IPC registration and the orchestrator factory."""

    def test_ipc_type_registered(self) -> None:
        """MemoryInvalidateRequest is dispatchable over the frontend IPC.

        Compared by name, not identity: the suite imports backend modules
        under two package prefixes, giving distinct class objects.
        """
        registered = FRONTEND_TYPE_MAP["MemoryInvalidateRequest"]
        assert registered.__name__ == "MemoryInvalidateRequest"
        assert set(MemoryInvalidateRequest.model_fields) <= set(
            registered.model_fields
        )

    @pytest.mark.asyncio
    async def test_factory_calls_socket_a_invalidate(self) -> None:
        """The factory invalidates via the orchestrator DB client (Socket A)."""

        class _FakeDBClient:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def invalidate_assertion(
                self,
                assertion_id: str,
                invalidated_by_assertion_id: str | None = None,
            ) -> bool:
                self.calls.append(assertion_id)
                assert invalidated_by_assertion_id is None
                return True

        db_client = _FakeDBClient()
        fn = make_memory_invalidate_fn(db_client)
        response = await fn(
            MemoryInvalidateRequest(
                request_id="r1", assertion_id="a1", reason="forget"
            )
        )
        assert response.invalidated is True
        assert db_client.calls == ["a1"]

    def test_web_socket_gains_no_graph_write(self) -> None:
        """S4.5 — Socket B allow-list holds no graph write operations."""
        for op in ("invalidate_assertion", "save_entity", "save_assertion"):
            assert op not in SOCKET_B_OPERATIONS
