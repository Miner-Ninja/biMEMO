"""Security tests for conversation-scoped structured tool grants."""

import json
import os

import pytest
from backend.permissions import ActionContext, ActionDecisionService
from backend.processes.db_proxy.handlers.agent_turn import (
    make_create_agent_turn_handler,
    make_upsert_agent_tool_batch_handler,
)
from backend.processes.db_proxy.handlers.permissions import (
    make_authorize_agent_invocation_handler,
    make_lookup_capability_grant_handler,
    make_revoke_capability_grant_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


def _open_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "tool-permissions.db"))
    db.open(os.urandom(32))
    initialize_schema(db)
    return db


async def _turn_with_web_search(
    db: DatabaseManager,
    *,
    turn_id: str,
    conversation_id: str,
    invocation_id: str,
    confirmation_id: str | None,
) -> None:
    await make_create_agent_turn_handler(db)(
        {
            "turn_id": turn_id,
            "finalization_id": f"final-{turn_id}",
            "conversation_id": conversation_id,
            "session_id": "session-1",
            "channel": "web",
        }
    )
    await make_upsert_agent_tool_batch_handler(db)(
        {
            "turn_id": turn_id,
            "batch_id": f"batch-{turn_id}",
            "assistant_message": {"role": "assistant", "tool_calls": []},
            "transcript_snapshot": [],
            "invocations": [
                {
                    "invocation_id": invocation_id,
                    "tool_call_id": f"call-{turn_id}",
                    "tool_name": "web_search",
                    "arguments": {"query": "current btc price"},
                    "confirmation_id": confirmation_id,
                    "confirmation_title": "web_search",
                    "confirmation_description": "Allow internet reads",
                    "confirmation_expires_at": (
                        "2999-01-01T00:00:00+00:00" if confirmation_id else None
                    ),
                }
            ],
        }
    )


async def test_conversation_scoped_grant_matrix(tmp_path) -> None:
    """Approval is reusable in one chat and authorization+claim is atomic."""
    db = _open_db(tmp_path)
    decision = ActionDecisionService().decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="web_search",
            parameters={"query": "current btc price"},
            structured_authority=True,
        )
    )
    scope = dict(decision.details["scope"])
    scope_hash = str(decision.details["scope_hash"])
    scope_value = "conversation-1"
    authorize = make_authorize_agent_invocation_handler(db)

    await _turn_with_web_search(
        db,
        turn_id="turn-1",
        conversation_id="conversation-1",
        invocation_id="invocation-1",
        confirmation_id="confirmation-1",
    )
    first = await authorize(
        {
            "turn_id": "turn-1",
            "invocation_id": "invocation-1",
            "idempotency_key": "dispatch-1",
            "capability": "web_search",
            "channel": "web",
            "scope_type": "conversation_capability",
            "scope_value": scope_value,
            "scope_json": scope,
            "scope_hash": scope_hash,
            "policy_revision": "1",
            "confirmation_id": "confirmation-1",
            "create_reusable_grant": True,
            "grant_expires_at": "2999-01-01T00:00:00+00:00",
            "expected_statuses": ["pending_confirmation"],
        }
    )
    assert first["claimed"] is True
    grant = (
        await make_lookup_capability_grant_handler(db)(
            {
                "scope_type": "conversation_capability",
                "scope_value": scope_value,
                "capability": "web_search",
                "channel": "web",
                "scope_hash": scope_hash,
                "policy_revision": "1",
            }
        )
    )["grant"]
    assert grant["origin_confirmation_id"] == "confirmation-1"
    assert grant["expires_at"] == "2999-01-01T00:00:00+00:00"

    scoped_service = ActionDecisionService()
    path_a = scoped_service.decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="file_read",
            parameters={"path": "./README.md"},
            structured_authority=True,
        )
    )
    path_a_equivalent = scoped_service.decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="file_read",
            parameters={"path": "README.md"},
            structured_authority=True,
        )
    )
    path_b = scoped_service.decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="file_read",
            parameters={"path": "SECURITY.md"},
            structured_authority=True,
        )
    )
    assert path_a.details["scope_hash"] == path_a_equivalent.details["scope_hash"]
    assert path_a.details["scope_hash"] != path_b.details["scope_hash"]

    sensitive_a = scoped_service.decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="email_send",
            parameters={"to": "a@example.test", "body": "hello"},
            structured_authority=True,
        )
    )
    sensitive_b = scoped_service.decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="email_send",
            parameters={"to": "b@example.test", "body": "hello"},
            structured_authority=True,
        )
    )
    assert sensitive_a.details["scope_hash"] != sensitive_b.details["scope_hash"]

    await _turn_with_web_search(
        db,
        turn_id="turn-2",
        conversation_id="conversation-1",
        invocation_id="invocation-2",
        confirmation_id=None,
    )
    reused = await authorize(
        {
            "turn_id": "turn-2",
            "invocation_id": "invocation-2",
            "idempotency_key": "dispatch-2",
            "capability": "web_search",
            "channel": "web",
            "scope_type": "conversation_capability",
            "scope_value": scope_value,
            "scope_json": scope,
            "scope_hash": scope_hash,
            "policy_revision": "1",
            "expected_statuses": ["required"],
        }
    )
    assert reused["claimed"] is True

    await _turn_with_web_search(
        db,
        turn_id="turn-other",
        conversation_id="conversation-2",
        invocation_id="invocation-other",
        confirmation_id=None,
    )
    with pytest.raises(PermissionError, match="another conversation"):
        await authorize(
            {
                "turn_id": "turn-other",
                "invocation_id": "invocation-other",
                "idempotency_key": "dispatch-other",
                "capability": "web_search",
                "channel": "web",
                "scope_type": "conversation_capability",
                "scope_value": scope_value,
                "scope_json": scope,
                "scope_hash": scope_hash,
                "policy_revision": "1",
                "expected_statuses": ["required"],
            }
        )

    await make_revoke_capability_grant_handler(db)({"id": grant["id"]})
    await _turn_with_web_search(
        db,
        turn_id="turn-3",
        conversation_id="conversation-1",
        invocation_id="invocation-3",
        confirmation_id=None,
    )
    with pytest.raises(PermissionError, match="no active compatible"):
        await authorize(
            {
                "turn_id": "turn-3",
                "invocation_id": "invocation-3",
                "idempotency_key": "dispatch-3",
                "capability": "web_search",
                "channel": "web",
                "scope_type": "conversation_capability",
                "scope_value": scope_value,
                "scope_json": scope,
                "scope_hash": scope_hash,
                "policy_revision": "1",
                "expected_statuses": ["required"],
            }
        )
    assert db.in_transaction is False
    db.close()


async def test_reusable_grant_requires_active_expiration(tmp_path) -> None:
    """Structured authorization rejects expired and legacy unbounded grants."""
    db = _open_db(tmp_path)
    decision = ActionDecisionService().decide(
        ActionContext(
            conversation_id="conversation-1",
            channel="web",
            session_id="session-1",
            tool_name="web_search",
            parameters={"query": "btc"},
            structured_authority=True,
        )
    )
    scope = dict(decision.details["scope"])
    scope_hash = str(decision.details["scope_hash"])
    await _turn_with_web_search(
        db,
        turn_id="turn-expired-grant",
        conversation_id="conversation-1",
        invocation_id="invocation-expired-grant",
        confirmation_id="confirmation-expired-grant",
    )
    authorize = make_authorize_agent_invocation_handler(db)
    with pytest.raises(ValueError, match="future"):
        await authorize(
            {
                "turn_id": "turn-expired-grant",
                "invocation_id": "invocation-expired-grant",
                "idempotency_key": "dispatch-expired-grant",
                "capability": "web_search",
                "channel": "web",
                "scope_type": "conversation_capability",
                "scope_value": "conversation-1",
                "scope_json": scope,
                "scope_hash": scope_hash,
                "policy_revision": "1",
                "confirmation_id": "confirmation-expired-grant",
                "create_reusable_grant": True,
                "grant_expires_at": "2000-01-01T00:00:00+00:00",
                "expected_statuses": ["pending_confirmation"],
            }
        )
    db.execute(
        "INSERT INTO capability_grants "
        "(id, scope_type, scope_value, capability, decision, channel, scope_json, "
        "scope_hash, policy_revision, expires_at) "
        "VALUES ('legacy-null', 'conversation_capability', 'conversation-1', "
        "'web_search', 'allow', 'web', ?, ?, '1', NULL)",
        (
            json.dumps(scope, sort_keys=True, separators=(",", ":")),
            scope_hash,
        ),
    )
    db.commit()
    await _turn_with_web_search(
        db,
        turn_id="turn-null-grant",
        conversation_id="conversation-1",
        invocation_id="invocation-null-grant",
        confirmation_id=None,
    )
    with pytest.raises(PermissionError, match="no active"):
        await authorize(
            {
                "turn_id": "turn-null-grant",
                "invocation_id": "invocation-null-grant",
                "idempotency_key": "dispatch-null-grant",
                "capability": "web_search",
                "channel": "web",
                "scope_type": "conversation_capability",
                "scope_value": "conversation-1",
                "scope_json": scope,
                "scope_hash": scope_hash,
                "policy_revision": "1",
                "expected_statuses": ["required"],
            }
        )
    db.close()
