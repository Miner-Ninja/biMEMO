"""Integration tests for the /confirm route realtime routing binding."""

from unittest.mock import AsyncMock

from backend.ipc.models import (
    ConfirmResponse as IPCConfirmResponse,
)
from tests.llm_config_fakes import _authed_client_with_csrf, _build_test_app


class TestConfirmRouteIntegration:
    """Integration tests for the /confirm route's realtime routing binding."""

    def test_confirm_decision_registers_and_unregisters_event_routing(
        self,
    ) -> None:
        """A confirm decision binds its request to the session, then unbinds."""
        app, token, sm, sc, _audit_log = _build_test_app()
        client, csrf = _authed_client_with_csrf(app, token, sm, sc)
        orchestrator_client = app.state.orchestrator_client
        orchestrator_client.send_confirm = AsyncMock(
            return_value=IPCConfirmResponse(
                request_id="confirm-ipc-1",
                action_id="action-1",
                accepted=True,
                status="final",
                message_id="message-1",
                result_summary="Confirmed.",
            )
        )
        jti = sc._tokens[token]["jti"]

        response = client.post(
            "/confirm",
            json={
                "action_id": "action-1",
                "decision": "approve",
                "scope": "once",
                "conversation_id": "conversation-1",
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["status"] == "final"

        (registered_request_id,) = (
            orchestrator_client.register_confirm_request.call_args.args
        )
        assert orchestrator_client.register_confirm_request.call_args.kwargs == {
            "session_id": jti,
            "conversation_id": "conversation-1",
        }

        (ipc_request,) = orchestrator_client.send_confirm.call_args.args
        assert ipc_request.request_id == registered_request_id
        assert ipc_request.action_id == "action-1"
        assert ipc_request.decision == "approve"
        assert ipc_request.scope == "once"

        (unregistered_request_id,) = (
            orchestrator_client.unregister_confirm_request.call_args.args
        )
        assert unregistered_request_id == registered_request_id
