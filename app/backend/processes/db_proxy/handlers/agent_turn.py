"""Core-database authority handlers for structured agent turns."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.agent.turn_state import arguments_hash

from ..common import OperationHandler, validate_params
from ..params import (
    AppendAgentTurnEventParams,
    CancelAgentTurnParams,
    ClaimAgentInvocationParams,
    CommitAgentTurnFinalizationParams,
    CompleteAgentInvocationParams,
    CreateAgentClarificationParams,
    CreateAgentPendingInputParams,
    CreateAgentTurnParams,
    DecideAgentClarificationParams,
    DecideAgentConfirmationParams,
    GetAgentConfirmationParams,
    GetAgentPendingInputParams,
    GetAgentTurnParams,
    GetPendingAgentClarificationParams,
    ListAgentTurnEventsParams,
    ListPendingAgentConfirmationsParams,
    ListUnprojectedAgentTurnEventsParams,
    MarkAgentTurnEventProjectedParams,
    RecoverAgentTurnsParams,
    ReplyAgentPendingInputParams,
    UpsertAgentToolBatchParams,
    UpsertAgentTurnObligationsParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


def _id() -> str:
    return str(uuid.uuid4())


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_json(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError, TypeError:
        return fallback


def _begin(db: DatabaseManager) -> None:
    db.execute("BEGIN IMMEDIATE")


def _rollback(db: DatabaseManager) -> None:
    db.execute("ROLLBACK")


def _turn_record(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "turn_id": row[0],
        "conversation_id": row[1],
        "session_id": row[2],
        "channel": row[3],
        "architecture_version": row[4],
        "tool_policy_version": row[5],
        "authority_store": row[6],
        "status": row[7],
        "terminal_outcome": row[8],
        "finalization_id": row[9],
        "active_time_ms": row[10],
        "provider_calls": row[11],
        "tool_rounds": row[12],
        "enforcement_reasks": row[13],
        "synthesis_attempts": row[14],
        "cancellation_requested": bool(row[15]),
        "terminal_error": _load_json(row[16], None),
        "outcome_payload": _load_json(row[17], None),
        "created_at": row[18],
        "updated_at": row[19],
    }


_TURN_COLUMNS = (
    "id, conversation_id, session_id, channel, architecture_version, "
    "tool_policy_version, "
    "authority_store, status, terminal_outcome, finalization_id, active_time_ms, "
    "provider_calls, tool_rounds, enforcement_reasks, synthesis_attempts, "
    "cancellation_requested, terminal_error, outcome_payload, created_at, updated_at"
)


def _read_turn(db: DatabaseManager, turn_id: str) -> dict[str, Any] | None:
    row = db.execute(
        f"SELECT {_TURN_COLUMNS} FROM agent_turns WHERE id = ?",  # noqa: S608
        (turn_id,),
    ).fetchone()
    return _turn_record(row) if row is not None else None


def _read_ledger(db: DatabaseManager, turn_id: str) -> dict[str, Any]:
    batches = db.execute(
        "SELECT id, assistant_message, transcript_snapshot FROM agent_tool_batches "
        "WHERE turn_id = ? ORDER BY created_at, id",
        (turn_id,),
    ).fetchall()
    obligations = db.execute(
        "SELECT id, required_tool, source, evidence_key, argument_predicate, status "
        "FROM agent_tool_obligations WHERE turn_id = ? ORDER BY created_at, id",
        (turn_id,),
    ).fetchall()
    invocations = db.execute(
        "SELECT id, batch_id, tool_call_id, tool_name, canonical_arguments, "
        "canonical_args_hash, policy_revision, evidence_key, source, status, "
        "claim_generation, idempotency_key, result_payload, error, confirmation_id "
        "FROM agent_tool_invocations WHERE turn_id = ? ORDER BY created_at, id",
        (turn_id,),
    ).fetchall()
    binds = db.execute(
        "SELECT b.obligation_id, b.invocation_id, b.evidence_key "
        "FROM agent_obligation_binds b JOIN agent_tool_obligations o "
        "ON o.id = b.obligation_id WHERE o.turn_id = ?",
        (turn_id,),
    ).fetchall()
    confirmations = db.execute(
        "SELECT c.id, c.invocation_id, c.status, c.title, c.description, "
        "c.expires_at, c.decided_at, i.tool_name, i.canonical_arguments "
        "FROM agent_confirmations c JOIN agent_tool_invocations i "
        "ON i.id = c.invocation_id WHERE c.turn_id = ? ORDER BY c.created_at, c.id",
        (turn_id,),
    ).fetchall()
    clarifications = db.execute(
        "SELECT id, proposed_tool, reason, status, expires_at, created_at "
        "FROM agent_clarifications WHERE turn_id = ? ORDER BY created_at, id",
        (turn_id,),
    ).fetchall()
    pending_inputs = db.execute(
        "SELECT id, question, expected_input_schema, status, cas_version, "
        "answer_payload, expires_at, created_at, suggestions "
        "FROM agent_pending_inputs "
        "WHERE turn_id = ? ORDER BY created_at, id",
        (turn_id,),
    ).fetchall()
    events = db.execute(
        "SELECT id, sequence, event_type, phase, attempt, payload, created_at "
        "FROM agent_turn_events WHERE turn_id = ? ORDER BY sequence",
        (turn_id,),
    ).fetchall()
    return {
        "batches": [
            {
                "batch_id": row[0],
                "assistant_message": _load_json(row[1], {}),
                "transcript_snapshot": _load_json(row[2], []),
            }
            for row in batches
        ],
        "obligations": [
            {
                "obligation_id": row[0],
                "required_tool": row[1],
                "source": row[2],
                "evidence_key": row[3],
                "argument_predicate": _load_json(row[4], None),
                "status": row[5],
            }
            for row in obligations
        ],
        "invocations": [
            {
                "invocation_id": row[0],
                "batch_id": row[1],
                "tool_call_id": row[2],
                "tool_name": row[3],
                "arguments": _load_json(row[4], {}),
                "canonical_args_hash": row[5],
                "policy_revision": row[6],
                "evidence_key": row[7],
                "source": row[8],
                "status": row[9],
                "claim_generation": row[10],
                "idempotency_key": row[11],
                "result": _load_json(row[12], None),
                "error": row[13],
                "confirmation_id": row[14],
            }
            for row in invocations
        ],
        "binds": [
            {
                "obligation_id": row[0],
                "invocation_id": row[1],
                "evidence_key": row[2],
            }
            for row in binds
        ],
        "confirmations": [
            {
                "confirmation_id": row[0],
                "invocation_id": row[1],
                "status": row[2],
                "title": row[3],
                "description": row[4],
                "expires_at": row[5],
                "decided_at": row[6],
                "tool_name": row[7],
                "parameters": _load_json(row[8], {}),
            }
            for row in confirmations
        ],
        "clarifications": [
            {
                "clarification_id": row[0],
                "proposed_tool": row[1],
                "reason": row[2],
                "status": row[3],
                "expires_at": row[4],
                "created_at": row[5],
            }
            for row in clarifications
        ],
        "pending_inputs": [
            {
                "pending_input_id": row[0],
                "question": row[1],
                "expected_input_schema": _load_json(row[2], {}),
                "status": row[3],
                "cas_version": row[4],
                "answer": _load_json(row[5], None),
                "expires_at": row[6],
                "created_at": row[7],
                "suggestions": _load_json(row[8], []),
            }
            for row in pending_inputs
        ],
        "events": [
            {
                "id": row[0],
                "run_id": turn_id,
                "sequence": row[1],
                "event_type": row[2],
                "phase": row[3],
                "attempt": row[4],
                "payload": _load_json(row[5], {}),
                "created_at": row[6],
            }
            for row in events
        ],
    }


def make_create_agent_turn_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreateAgentTurnParams)
        turn_id = p.turn_id or _id()
        existing_turn = _read_turn(db, turn_id)
        finalization_id = p.finalization_id or (
            str(existing_turn["finalization_id"])
            if existing_turn is not None
            else _id()
        )
        db.execute(
            "INSERT OR IGNORE INTO conversations (id, title) VALUES (?, '')",
            (p.conversation_id,),
        )
        db.execute(
            "INSERT OR IGNORE INTO agent_turns "
            "(id, conversation_id, session_id, channel, architecture_version, "
            "tool_policy_version, authority_store, finalization_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                turn_id,
                p.conversation_id,
                p.session_id,
                p.channel,
                p.architecture_version,
                p.tool_policy_version,
                "core",
                finalization_id,
            ),
        )
        db.commit()
        turn = _read_turn(db, turn_id)
        if turn is None:
            raise RuntimeError("agent turn creation failed")
        expected = (
            p.conversation_id,
            p.architecture_version,
            p.tool_policy_version,
            "core",
            finalization_id,
        )
        actual = (
            turn["conversation_id"],
            turn["architecture_version"],
            turn["tool_policy_version"],
            turn["authority_store"],
            turn["finalization_id"],
        )
        if actual != expected:
            raise ValueError("agent turn replay payload does not match stored identity")
        return {"turn": turn}

    return handle


def make_get_agent_turn_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetAgentTurnParams)
        _expire_pending_inputs(db, turn_id=p.turn_id)
        turn = _read_turn(db, p.turn_id)
        if turn is None:
            return {"turn": None}
        return {"turn": {**turn, **_read_ledger(db, p.turn_id)}}

    return handle


def make_upsert_agent_turn_obligations_handler(
    db: DatabaseManager,
) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, UpsertAgentTurnObligationsParams)
        try:
            _begin(db)
            for obligation in p.obligations:
                predicate = (
                    _json(obligation.argument_predicate)
                    if obligation.argument_predicate is not None
                    else None
                )
                db.execute(
                    "INSERT OR IGNORE INTO agent_tool_obligations "
                    "(id, turn_id, required_tool, source, evidence_key, "
                    "argument_predicate) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        obligation.obligation_id,
                        p.turn_id,
                        obligation.required_tool,
                        obligation.source,
                        obligation.evidence_key,
                        predicate,
                    ),
                )
                row = db.execute(
                    "SELECT turn_id, required_tool, source, evidence_key, "
                    "argument_predicate FROM agent_tool_obligations WHERE id = ?",
                    (obligation.obligation_id,),
                ).fetchone()
                if row != (
                    p.turn_id,
                    obligation.required_tool,
                    obligation.source,
                    obligation.evidence_key,
                    predicate,
                ):
                    raise ValueError("obligation replay payload mismatch")
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"turn_id": p.turn_id, "obligation_count": len(p.obligations)}

    return handle


def make_upsert_agent_tool_batch_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, UpsertAgentToolBatchParams)
        try:
            _begin(db)
            db.execute(
                "INSERT OR IGNORE INTO agent_tool_batches "
                "(id, turn_id, assistant_message, transcript_snapshot) "
                "VALUES (?, ?, ?, ?)",
                (
                    p.batch_id,
                    p.turn_id,
                    _json(p.assistant_message),
                    _json(p.transcript_snapshot),
                ),
            )
            batch_row = db.execute(
                "SELECT turn_id, assistant_message, transcript_snapshot "
                "FROM agent_tool_batches WHERE id = ?",
                (p.batch_id,),
            ).fetchone()
            expected_batch = (
                p.turn_id,
                _json(p.assistant_message),
                _json(p.transcript_snapshot),
            )
            if batch_row != expected_batch:
                raise ValueError("tool batch replay payload mismatch")
            for invocation in p.invocations:
                arguments_json = _json(invocation.arguments)
                if (
                    arguments_hash(invocation.arguments)
                    != invocation.canonical_args_hash
                ):
                    raise ValueError("canonical invocation hash mismatch")
                status = (
                    "pending_confirmation"
                    if invocation.confirmation_id is not None
                    else "required"
                )
                db.execute(
                    "INSERT OR IGNORE INTO agent_tool_invocations "
                    "(id, turn_id, batch_id, tool_call_id, tool_name, "
                    "canonical_arguments, canonical_args_hash, evidence_key, source, "
                    "policy_revision, status, confirmation_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        invocation.invocation_id,
                        p.turn_id,
                        p.batch_id,
                        invocation.tool_call_id,
                        invocation.tool_name,
                        arguments_json,
                        invocation.canonical_args_hash,
                        invocation.evidence_key,
                        invocation.source,
                        invocation.policy_revision,
                        status,
                        invocation.confirmation_id,
                    ),
                )
                invocation_row = db.execute(
                    "SELECT turn_id, batch_id, tool_call_id, tool_name, "
                    "canonical_arguments, canonical_args_hash, source, "
                    "policy_revision, confirmation_id "
                    "FROM agent_tool_invocations WHERE id = ?",
                    (invocation.invocation_id,),
                ).fetchone()
                expected_invocation = (
                    p.turn_id,
                    p.batch_id,
                    invocation.tool_call_id,
                    invocation.tool_name,
                    arguments_json,
                    invocation.canonical_args_hash,
                    invocation.source,
                    invocation.policy_revision,
                    invocation.confirmation_id,
                )
                if invocation_row != expected_invocation:
                    raise ValueError("tool invocation replay payload mismatch")
                for obligation_id in invocation.candidate_obligation_ids:
                    db.execute(
                        "INSERT OR IGNORE INTO agent_obligation_candidates "
                        "(obligation_id, invocation_id) VALUES (?, ?)",
                        (obligation_id, invocation.invocation_id),
                    )
                if invocation.confirmation_id is not None:
                    if invocation.confirmation_expires_at is None:
                        raise ValueError("confirmation expiration is required")
                    db.execute(
                        "INSERT OR IGNORE INTO agent_confirmations "
                        "(id, turn_id, invocation_id, title, description, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            invocation.confirmation_id,
                            p.turn_id,
                            invocation.invocation_id,
                            invocation.confirmation_title,
                            invocation.confirmation_description,
                            invocation.confirmation_expires_at,
                        ),
                    )
            if any(item.confirmation_id is not None for item in p.invocations):
                db.execute(
                    "UPDATE agent_turns SET status = 'paused', "
                    "terminal_outcome = 'paused', updated_at = "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                    "AND terminal_outcome IS NULL",
                    (p.turn_id,),
                )
                sequence_row = db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 "
                    "FROM agent_turn_events WHERE turn_id = ?",
                    (p.turn_id,),
                ).fetchone()
                confirmations = [
                    {
                        "action_id": item.confirmation_id,
                        "invocation_id": item.invocation_id,
                        "tool_name": item.tool_name,
                        "parameters": item.arguments,
                    }
                    for item in p.invocations
                    if item.confirmation_id is not None
                ]
                db.execute(
                    "INSERT INTO agent_turn_events "
                    "(id, turn_id, sequence, event_type, phase, attempt, payload) "
                    "VALUES (?, ?, ?, 'run_paused', 'confirmation', 0, ?)",
                    (
                        _id(),
                        p.turn_id,
                        int(sequence_row[0]),
                        _json(
                            {
                                "run_id": p.turn_id,
                                "pending_confirmations": confirmations,
                            }
                        ),
                    ),
                )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"turn_id": p.turn_id, "batch_id": p.batch_id}

    return handle


def make_claim_agent_invocation_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ClaimAgentInvocationParams)
        try:
            _begin(db)
            turn = _read_turn(db, p.turn_id)
            if turn is None:
                raise ValueError("unknown agent turn")
            if turn["terminal_outcome"] not in (None, "paused"):
                raise RuntimeError("terminal turn cannot dispatch another invocation")
            row = db.execute(
                "SELECT status, claim_generation, idempotency_key FROM "
                "agent_tool_invocations WHERE id = ? AND turn_id = ?",
                (p.invocation_id, p.turn_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown invocation")
            if row[0] == "executing" and row[2] == p.idempotency_key:
                db.commit()
                return {
                    "claimed": False,
                    "status": row[0],
                    "claim_generation": row[1],
                }
            if row[0] != "required":
                raise RuntimeError(f"invocation cannot be claimed from {row[0]}")
            generation = int(row[1]) + 1
            db.execute(
                "UPDATE agent_tool_invocations SET status = 'executing', "
                "claim_generation = ?, idempotency_key = ?, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? AND turn_id = ?",
                (generation, p.idempotency_key, p.invocation_id, p.turn_id),
            )
            db.execute(
                "UPDATE agent_turns SET status = 'tool_execution', "
                "terminal_outcome = NULL, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (p.turn_id,),
            )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"claimed": True, "status": "executing", "claim_generation": generation}

    return handle


def _argument_predicate_matches(
    predicate_json: str | None, arguments: dict[str, Any]
) -> bool:
    predicate = _load_json(predicate_json, None)
    if predicate is None:
        return True
    if not isinstance(predicate, dict):
        return False
    return all(arguments.get(key) == value for key, value in predicate.items())


def make_complete_agent_invocation_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CompleteAgentInvocationParams)
        try:
            _begin(db)
            row = db.execute(
                "SELECT status, claim_generation, canonical_arguments, tool_name, "
                "evidence_key FROM agent_tool_invocations WHERE id = ? AND turn_id = ?",
                (p.invocation_id, p.turn_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown invocation")
            if row[0] == "succeeded":
                db.commit()
                return {"stored": False, "status": "succeeded", "bound": []}
            if row[0] == "unknown" and int(row[1]) == p.claim_generation:
                turn = _read_turn(db, p.turn_id)
                if turn is not None and turn["cancellation_requested"]:
                    db.execute(
                        "UPDATE agent_tool_invocations SET result_payload = ?, "
                        "error = ?, updated_at = "
                        "strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                        "WHERE id = ? AND turn_id = ?",
                        (
                            _json(p.result) if p.result is not None else None,
                            p.error,
                            p.invocation_id,
                            p.turn_id,
                        ),
                    )
                    db.commit()
                    return {"stored": True, "status": "unknown", "bound": []}
            if row[0] != "executing" or int(row[1]) != p.claim_generation:
                raise RuntimeError("stale invocation callback generation")
            result_json = _json(p.result) if p.result is not None else None
            db.execute(
                "UPDATE agent_tool_invocations SET status = ?, result_payload = ?, "
                "error = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE id = ? AND turn_id = ?",
                (p.status, result_json, p.error, p.invocation_id, p.turn_id),
            )
            bound: list[str] = []
            turn = _read_turn(db, p.turn_id)
            if (
                p.status == "succeeded"
                and turn is not None
                and not turn["terminal_outcome"]
            ):
                arguments = _load_json(row[2], {})
                candidates = db.execute(
                    "SELECT o.id, o.evidence_key, o.argument_predicate "
                    "FROM agent_obligation_candidates c "
                    "JOIN agent_tool_obligations o ON o.id = c.obligation_id "
                    "WHERE c.invocation_id = ? AND o.turn_id = ? "
                    "AND o.required_tool = ? AND o.status = 'open' "
                    "ORDER BY o.created_at, o.id",
                    (p.invocation_id, p.turn_id, row[3]),
                ).fetchall()
                compatible = [
                    item
                    for item in candidates
                    if isinstance(arguments, dict)
                    and _argument_predicate_matches(item[2], arguments)
                ]
                if compatible:
                    first_key = str(compatible[0][1] or "")
                    selected = (
                        [item for item in compatible if item[1] == first_key]
                        if first_key
                        else compatible[:1]
                    )
                    for obligation_id, evidence_key, _predicate in selected:
                        db.execute(
                            "INSERT INTO agent_obligation_binds "
                            "(obligation_id, invocation_id, evidence_key) "
                            "VALUES (?, ?, ?)",
                            (obligation_id, p.invocation_id, evidence_key or ""),
                        )
                        db.execute(
                            "UPDATE agent_tool_obligations SET status = 'satisfied', "
                            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                            "WHERE id = ?",
                            (obligation_id,),
                        )
                        bound.append(str(obligation_id))
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"stored": True, "status": p.status, "bound": bound}

    return handle


def make_decide_agent_confirmation_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, DecideAgentConfirmationParams)
        try:
            _begin(db)
            row = db.execute(
                "SELECT turn_id, invocation_id, status, expires_at, "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "FROM agent_confirmations "
                "WHERE id = ?",
                (p.confirmation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown confirmation")
            if row[2] == "pending" and str(row[3]) <= str(row[4]):
                db.execute(
                    "UPDATE agent_confirmations SET status = 'expired', decided_at = "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                    (p.confirmation_id,),
                )
                db.execute(
                    "UPDATE agent_tool_invocations SET status = 'expired', "
                    "updated_at = "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                    "AND status = 'pending_confirmation'",
                    (row[1],),
                )
                db.commit()
                return {
                    "turn_id": row[0],
                    "invocation_id": row[1],
                    "status": "expired",
                    "changed": True,
                }
            if row[2] != "pending":
                db.commit()
                return {
                    "turn_id": row[0],
                    "invocation_id": row[1],
                    "status": row[2],
                    "changed": False,
                }
            db.execute(
                "UPDATE agent_confirmations SET status = ?, decided_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (p.decision, p.confirmation_id),
            )
            if p.decision in {"denied", "expired"}:
                db.execute(
                    "UPDATE agent_tool_invocations SET status = ?, updated_at = "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                    "AND status = 'pending_confirmation'",
                    (p.decision, row[1]),
                )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {
            "turn_id": row[0],
            "invocation_id": row[1],
            "status": p.decision,
            "changed": True,
        }

    return handle


def make_get_agent_confirmation_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetAgentConfirmationParams)
        row = db.execute(
            "SELECT c.id, c.turn_id, c.invocation_id, c.status, c.title, "
            "c.description, c.expires_at, i.tool_name, i.canonical_arguments, "
            "t.conversation_id, t.channel FROM agent_confirmations c "
            "JOIN agent_tool_invocations i ON i.id = c.invocation_id "
            "JOIN agent_turns t ON t.id = c.turn_id WHERE c.id = ?",
            (p.confirmation_id,),
        ).fetchone()
        if row is None:
            return {"confirmation": None}
        return {
            "confirmation": {
                "confirmation_id": row[0],
                "turn_id": row[1],
                "invocation_id": row[2],
                "status": row[3],
                "title": row[4],
                "description": row[5],
                "expires_at": row[6],
                "tool_name": row[7],
                "arguments": _load_json(row[8], {}),
                "conversation_id": row[9],
                "channel": row[10],
            }
        }

    return handle


def make_list_pending_agent_confirmations_handler(
    db: DatabaseManager,
) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListPendingAgentConfirmationsParams)
        rows = db.execute(
            "SELECT c.id, c.turn_id, c.invocation_id, c.status, c.title, "
            "c.description, c.expires_at, i.tool_name, i.canonical_arguments, "
            "t.conversation_id, t.channel FROM agent_confirmations c "
            "JOIN agent_tool_invocations i ON i.id = c.invocation_id "
            "JOIN agent_turns t ON t.id = c.turn_id "
            "WHERE t.conversation_id = ? AND c.status = 'pending' "
            "AND c.expires_at > strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "ORDER BY c.created_at, c.id",
            (p.conversation_id,),
        ).fetchall()
        return {
            "confirmations": [
                {
                    "confirmation_id": row[0],
                    "turn_id": row[1],
                    "invocation_id": row[2],
                    "status": row[3],
                    "title": row[4],
                    "description": row[5],
                    "expires_at": row[6],
                    "tool_name": row[7],
                    "arguments": _load_json(row[8], {}),
                    "conversation_id": row[9],
                    "channel": row[10],
                }
                for row in rows
            ]
        }

    return handle


def make_append_agent_turn_event_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, AppendAgentTurnEventParams)
        active_time_delta = 0
        if p.event_type == "active_time_recorded":
            raw_delta = p.payload.get("duration_ms", 0)
            if not isinstance(raw_delta, int) or raw_delta < 0:
                raise ValueError("active duration must be a non-negative integer")
            active_time_delta = raw_delta
        event_id = p.event_id or _id()
        try:
            _begin(db)
            row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_turn_events "
                "WHERE turn_id = ?",
                (p.turn_id,),
            ).fetchone()
            sequence = int(row[0])
            db.execute(
                "INSERT OR IGNORE INTO agent_turn_events "
                "(id, turn_id, sequence, event_type, phase, attempt, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    p.turn_id,
                    sequence,
                    p.event_type,
                    p.phase,
                    p.attempt,
                    _json(p.payload),
                ),
            )
            db.execute(
                "UPDATE agent_turns SET provider_calls = provider_calls + ?, "
                "tool_rounds = tool_rounds + ?, "
                "enforcement_reasks = enforcement_reasks + ?, "
                "synthesis_attempts = synthesis_attempts + ?, "
                "active_time_ms = active_time_ms + ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (
                    1 if p.event_type == "provider_attempt" else 0,
                    1 if p.event_type == "tool_round_started" else 0,
                    1
                    if p.event_type
                    in {
                        "exact_force_ignored",
                        "native_decision_invalid",
                        "finish_rejected",
                    }
                    else 0,
                    1
                    if p.event_type == "provider_attempt" and p.phase == "Stage 2"
                    else 0,
                    active_time_delta,
                    p.turn_id,
                ),
            )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"event_id": event_id, "sequence": sequence}

    return handle


def make_cancel_agent_turn_handler(db: DatabaseManager) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CancelAgentTurnParams)
        try:
            _begin(db)
            turn = _read_turn(db, p.turn_id)
            if turn is None:
                raise ValueError("unknown agent turn")
            if turn["terminal_outcome"] in {"final", "failed", "cancelled"}:
                db.commit()
                return {"won": False, "turn": turn}
            outcome = "failed" if p.timeout or p.failure else "cancelled"
            error = {
                "code": (
                    "timeout"
                    if p.timeout
                    else "scheduler_failed"
                    if p.failure
                    else "cancelled"
                ),
                "message": p.reason,
            }
            db.execute(
                "UPDATE agent_tool_invocations SET status = 'unknown', error = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE turn_id = ? AND status = 'executing'",
                (p.reason, p.turn_id),
            )
            db.execute(
                "UPDATE agent_turns SET status = ?, terminal_outcome = ?, "
                "cancellation_requested = 1, terminal_error = ?, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (outcome, outcome, _json(error), p.turn_id),
            )
            db.execute(
                "UPDATE agent_confirmations SET status = 'expired', decided_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE turn_id = ? "
                "AND status = 'pending'",
                (p.turn_id,),
            )
            db.execute(
                "UPDATE agent_tool_invocations SET status = 'expired', "
                "error = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE turn_id = ? AND status = 'pending_confirmation'",
                (p.reason, p.turn_id),
            )
            if p.task_result is not None:
                task_result = p.task_result
                db.execute(
                    "INSERT INTO scheduled_task_runs "
                    "(id, task_id, started_at, finished_at, status, "
                    "result_text, error_message, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        turn["finalization_id"],
                        task_result.task_id,
                        task_result.started_at,
                        task_result.finished_at,
                        task_result.status,
                        task_result.result_text[:4000],
                        task_result.error_message[:1000],
                        task_result.duration_ms,
                    ),
                )
            sequence_row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_turn_events "
                "WHERE turn_id = ?",
                (p.turn_id,),
            ).fetchone()
            db.execute(
                "INSERT INTO agent_turn_events "
                "(id, turn_id, sequence, event_type, phase, attempt, payload) "
                "VALUES (?, ?, ?, ?, 'cancellation', 0, ?)",
                (
                    _id(),
                    p.turn_id,
                    int(sequence_row[0]),
                    "run_failed" if outcome == "failed" else "run_cancelled",
                    _json(error),
                ),
            )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"won": True, "turn": _read_turn(db, p.turn_id)}

    return handle


def make_commit_agent_turn_finalization_handler(
    db: DatabaseManager,
) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CommitAgentTurnFinalizationParams)
        canonical_payload = _json(p.outcome_payload)
        try:
            _begin(db)
            turn = _read_turn(db, p.turn_id)
            if turn is None:
                raise ValueError("unknown agent turn")
            if turn["finalization_id"] != p.finalization_id:
                raise ValueError("finalization identity does not match turn")
            existing = db.execute(
                "SELECT outcome_kind, canonical_payload, assistant_message_id, "
                "record_queue_id FROM agent_turn_finalizations WHERE id = ? "
                "AND turn_id = ?",
                (p.finalization_id, p.turn_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != p.outcome_kind or existing[1] != canonical_payload:
                    raise ValueError("finalization replay payload mismatch")
                if (existing[2] is not None) != p.persist_chat:
                    raise ValueError("finalization replay persistence mode mismatch")
                db.commit()
                return {
                    "replayed": True,
                    "outcome_kind": existing[0],
                    "finalization_id": p.finalization_id,
                    "assistant_message_id": existing[2],
                    "record_queue_id": existing[3],
                    "outcome_payload": p.outcome_payload,
                }
            if turn["terminal_outcome"] not in (None, "paused"):
                raise RuntimeError("a different terminal outcome already won")
            assistant_message_id: str | None = None
            record_queue_id: str | None = None
            if p.persist_chat:
                assistant_message_id = _id()
                assistant_meta = dict(p.assistant_meta or {})
                assistant_meta["finalization_id"] = p.finalization_id
                db.execute(
                    "INSERT INTO messages "
                    "(id, conversation_id, role, content, meta) "
                    "VALUES (?, ?, 'assistant', ?, ?)",
                    (
                        assistant_message_id,
                        p.conversation_id,
                        p.assistant_content,
                        _json(assistant_meta),
                    ),
                )
                if p.dialog_content:
                    record_queue_id = _id()
                    db.execute(
                        "INSERT INTO record_queue "
                        "(id, conversation_id, session_id, messages, "
                        "source_message_ids) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            record_queue_id,
                            p.conversation_id,
                            p.session_id,
                            _json([p.dialog_content]),
                            _json([*p.source_message_ids, assistant_message_id]),
                        ),
                    )
            if p.task_result is not None:
                task_result = p.task_result
                db.execute(
                    "INSERT INTO scheduled_task_runs "
                    "(id, task_id, started_at, finished_at, status, "
                    "result_text, error_message, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        p.finalization_id,
                        task_result.task_id,
                        task_result.started_at,
                        task_result.finished_at,
                        task_result.status,
                        task_result.result_text[:4000],
                        task_result.error_message[:1000],
                        task_result.duration_ms,
                    ),
                )
                db.execute(
                    "DELETE FROM scheduled_task_runs WHERE task_id = ? "
                    "AND id NOT IN (SELECT id FROM scheduled_task_runs "
                    "WHERE task_id = ? ORDER BY started_at DESC LIMIT 50)",
                    (task_result.task_id, task_result.task_id),
                )
            db.execute(
                "INSERT INTO agent_turn_finalizations "
                "(id, turn_id, outcome_kind, canonical_payload, "
                "assistant_message_id, record_queue_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    p.finalization_id,
                    p.turn_id,
                    p.outcome_kind,
                    canonical_payload,
                    assistant_message_id,
                    record_queue_id,
                ),
            )
            db.execute(
                "UPDATE agent_turns SET status = ?, terminal_outcome = ?, "
                "outcome_payload = ?, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (p.outcome_kind, p.outcome_kind, canonical_payload, p.turn_id),
            )
            db.execute(
                "UPDATE agent_confirmations SET status = 'expired', decided_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE turn_id = ? "
                "AND status = 'pending'",
                (p.turn_id,),
            )
            db.execute(
                "UPDATE agent_tool_invocations SET status = 'expired', "
                "error = 'turn finalized', updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE turn_id = ? "
                "AND status = 'pending_confirmation'",
                (p.turn_id,),
            )
            sequence_row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_turn_events "
                "WHERE turn_id = ?",
                (p.turn_id,),
            ).fetchone()
            db.execute(
                "INSERT INTO agent_turn_events "
                "(id, turn_id, sequence, event_type, phase, attempt, payload) "
                "VALUES (?, ?, ?, ?, 'terminal_commit', 0, ?)",
                (
                    _id(),
                    p.turn_id,
                    int(sequence_row[0]),
                    "final_accepted" if p.outcome_kind == "final" else "run_failed",
                    _json(
                        {
                            **p.outcome_payload,
                            "finalization_id": p.finalization_id,
                            "outcome_kind": p.outcome_kind,
                        }
                    ),
                ),
            )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {
            "replayed": False,
            "outcome_kind": p.outcome_kind,
            "finalization_id": p.finalization_id,
            "assistant_message_id": assistant_message_id,
            "record_queue_id": record_queue_id,
            "outcome_payload": p.outcome_payload,
        }

    return handle


def make_list_unprojected_agent_turn_events_handler(
    db: DatabaseManager,
) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListUnprojectedAgentTurnEventsParams)
        rows = db.execute(
            "SELECT e.id, e.turn_id, e.sequence, e.event_type, e.phase, e.attempt, "
            "e.payload, e.projection_version, e.created_at, t.conversation_id, "
            "t.session_id, t.channel FROM agent_turn_events e JOIN agent_turns t "
            "ON t.id = e.turn_id WHERE e.projected_at IS NULL "
            "ORDER BY e.created_at, e.turn_id, e.sequence LIMIT ?",
            (p.limit,),
        ).fetchall()
        return {
            "events": [
                {
                    "event_id": row[0],
                    "turn_id": row[1],
                    "sequence": row[2],
                    "event_type": row[3],
                    "phase": row[4],
                    "attempt": row[5],
                    "payload": _load_json(row[6], {}),
                    "projection_version": row[7],
                    "created_at": row[8],
                    "conversation_id": row[9],
                    "session_id": row[10],
                    "channel": row[11],
                }
                for row in rows
            ]
        }

    return handle


def make_list_agent_turn_events_handler(db: DatabaseManager) -> OperationHandler:
    """Read canonical events for one run or conversation reconnect."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListAgentTurnEventsParams)
        if not p.turn_id and not p.conversation_id:
            raise ValueError("turn_id or conversation_id is required")
        select = (
            "SELECT e.id, e.turn_id, e.sequence, e.event_type, e.phase, "
            "e.attempt, e.payload, e.created_at FROM agent_turn_events e "
            "JOIN agent_turns t ON t.id = e.turn_id WHERE "
        )
        order = " ORDER BY e.created_at, e.turn_id, e.sequence LIMIT ?"
        if p.turn_id and p.conversation_id:
            rows = db.execute(
                select
                + "e.sequence > ? AND e.turn_id = ? AND t.conversation_id = ?"
                + order,
                (p.after_sequence, p.turn_id, p.conversation_id, p.limit),
            ).fetchall()
        elif p.turn_id:
            rows = db.execute(
                select + "e.sequence > ? AND e.turn_id = ?" + order,
                (p.after_sequence, p.turn_id, p.limit),
            ).fetchall()
        else:
            if p.after_sequence == 0:
                rows = db.execute(
                    select + "t.conversation_id = ? ORDER BY e.created_at DESC, "
                    "e.turn_id DESC, e.sequence DESC LIMIT ?",
                    (p.conversation_id, p.limit),
                ).fetchall()[::-1]
            else:
                rows = db.execute(
                    select + "e.sequence > ? AND t.conversation_id = ?" + order,
                    (p.after_sequence, p.conversation_id, p.limit),
                ).fetchall()
        return {
            "events": [
                {
                    "id": row[0],
                    "run_id": row[1],
                    "sequence": row[2],
                    "event_type": row[3],
                    "phase": row[4],
                    "attempt": row[5],
                    "payload": _load_json(row[6], {}),
                    "created_at": row[7],
                }
                for row in rows
            ]
        }

    return handle


def make_mark_agent_turn_event_projected_handler(
    db: DatabaseManager,
) -> OperationHandler:
    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, MarkAgentTurnEventProjectedParams)
        cursor = db.execute(
            "UPDATE agent_turn_events SET projected_at = "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now'), projection_version = ? "
            "WHERE id = ?",
            (p.projection_version, p.event_id),
        )
        db.commit()
        return {"updated": cursor.rowcount == 1}

    return handle


def make_recover_agent_turns_handler(db: DatabaseManager) -> OperationHandler:
    """Fence every in-flight claim left by a previous orchestrator process."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        validate_params(params, RecoverAgentTurnsParams)
        try:
            _begin(db)
            rows = db.execute(
                "SELECT id, turn_id FROM agent_tool_invocations "
                "WHERE status = 'executing'"
            ).fetchall()
            for invocation_id, turn_id in rows:
                db.execute(
                    "UPDATE agent_tool_invocations SET status = 'unknown', "
                    "error = 'orchestrator_restarted_after_dispatch', updated_at = "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                    "AND status = 'executing'",
                    (invocation_id,),
                )
                sequence_row = db.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 "
                    "FROM agent_turn_events WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()
                db.execute(
                    "INSERT INTO agent_turn_events "
                    "(id, turn_id, sequence, event_type, phase, attempt, payload) "
                    "VALUES (?, ?, ?, 'invocation_fenced_unknown', "
                    "'recovery', 0, ?)",
                    (
                        _id(),
                        turn_id,
                        int(sequence_row[0]),
                        _json({"invocation_id": invocation_id}),
                    ),
                )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"recovered": len(rows)}

    return handle


def make_create_agent_clarification_handler(db: DatabaseManager) -> OperationHandler:
    """Persist or replay one low-confidence clarification proposal."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreateAgentClarificationParams)
        db.execute(
            "INSERT OR IGNORE INTO agent_clarifications "
            "(id, turn_id, proposed_tool, reason, expires_at) VALUES (?, ?, ?, ?, ?)",
            (
                p.clarification_id,
                p.turn_id,
                p.proposed_tool,
                p.reason,
                p.expires_at,
            ),
        )
        db.commit()
        row = db.execute(
            "SELECT turn_id, proposed_tool, reason, status, expires_at "
            "FROM agent_clarifications WHERE id = ?",
            (p.clarification_id,),
        ).fetchone()
        if row is None or (*row[:3], row[4]) != (
            p.turn_id,
            p.proposed_tool,
            p.reason,
            p.expires_at,
        ):
            raise ValueError("clarification replay payload mismatch")
        return {
            "clarification_id": p.clarification_id,
            "turn_id": row[0],
            "proposed_tool": row[1],
            "reason": row[2],
            "status": row[3],
            "expires_at": row[4],
        }

    return handle


def make_get_pending_agent_clarification_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Expire stale proposals and return the latest live conversation proposal."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetPendingAgentClarificationParams)
        db.execute(
            "UPDATE agent_clarifications SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at <= "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now')"
        )
        db.commit()
        row = db.execute(
            "SELECT c.id, c.turn_id, c.proposed_tool, c.reason, c.status, "
            "c.expires_at FROM agent_clarifications c JOIN agent_turns t "
            "ON t.id = c.turn_id WHERE t.conversation_id = ? "
            "AND c.status = 'pending' ORDER BY c.created_at DESC LIMIT 1",
            (p.conversation_id,),
        ).fetchone()
        if row is None:
            return {"clarification": None}
        return {
            "clarification": {
                "clarification_id": row[0],
                "turn_id": row[1],
                "proposed_tool": row[2],
                "reason": row[3],
                "status": row[4],
                "expires_at": row[5],
            }
        }

    return handle


def make_decide_agent_clarification_handler(db: DatabaseManager) -> OperationHandler:
    """CAS one pending clarification to its classified terminal state."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, DecideAgentClarificationParams)
        cursor = db.execute(
            "UPDATE agent_clarifications SET status = ? WHERE id = ? "
            "AND status = 'pending'",
            (p.decision, p.clarification_id),
        )
        db.commit()
        row = db.execute(
            "SELECT turn_id, proposed_tool, reason, status, expires_at "
            "FROM agent_clarifications WHERE id = ?",
            (p.clarification_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown clarification")
        return {
            "changed": cursor.rowcount == 1,
            "clarification_id": p.clarification_id,
            "turn_id": row[0],
            "proposed_tool": row[1],
            "reason": row[2],
            "status": row[3],
            "expires_at": row[4],
        }

    return handle


def _pending_input_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "pending_input_id": row[0],
        "turn_id": row[1],
        "question": row[2],
        "expected_input_schema": json.loads(row[3]),
        "transcript_snapshot": json.loads(row[4]),
        "status": row[5],
        "cas_version": int(row[6]),
        "answer": json.loads(row[7]) if row[7] is not None else None,
        "expires_at": row[8],
        "suggestions": _load_json(row[9], []),
    }


_PENDING_INPUT_EXPIRED_MESSAGE = "The requested input expired before it was received."


def _expire_pending_inputs(
    db: DatabaseManager,
    *,
    turn_id: str | None = None,
) -> None:
    """Atomically terminalize expired missing-input pauses in core authority."""
    try:
        _begin(db)
        query = (
            "SELECT p.id, p.turn_id, t.conversation_id, t.session_id, "
            "t.finalization_id FROM agent_pending_inputs p JOIN agent_turns t "
            "ON t.id = p.turn_id WHERE p.status = 'pending' "
            "AND p.expires_at <= strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "AND t.terminal_outcome = 'paused'"
        )
        if turn_id is None:
            rows = db.execute(query).fetchall()
        else:
            rows = db.execute(query + " AND t.id = ?", (turn_id,)).fetchall()
        for (
            pending_id,
            expired_turn_id,
            conversation_id,
            session_id,
            finalization_id,
        ) in rows:
            error = {
                "code": "pending_input_expired",
                "message": _PENDING_INPUT_EXPIRED_MESSAGE,
                "retryable": False,
            }
            structured = {
                "answer": _PENDING_INPUT_EXPIRED_MESSAGE,
                "thinking": None,
                "suggestions": [],
            }
            payload = {
                "structured_content": structured,
                "input_filtered": False,
                "output_filtered": False,
                "stats": None,
                "error": error,
            }
            assistant_message_id = _id()
            record_queue_id = _id()
            db.execute(
                "UPDATE agent_pending_inputs SET status = 'expired', cas_version = "
                "cas_version + 1, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                "AND status = 'pending'",
                (pending_id,),
            )
            db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, meta) "
                "VALUES (?, ?, 'assistant', ?, ?)",
                (
                    assistant_message_id,
                    conversation_id,
                    _PENDING_INPUT_EXPIRED_MESSAGE,
                    _json(
                        {
                            "structured_content": structured,
                            "finalization_id": finalization_id,
                        }
                    ),
                ),
            )
            db.execute(
                "INSERT INTO record_queue "
                "(id, conversation_id, session_id, messages, source_message_ids) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    record_queue_id,
                    conversation_id,
                    session_id,
                    _json([f"Assistant: {_PENDING_INPUT_EXPIRED_MESSAGE}"]),
                    _json([assistant_message_id]),
                ),
            )
            db.execute(
                "INSERT INTO agent_turn_finalizations "
                "(id, turn_id, outcome_kind, canonical_payload, "
                "assistant_message_id, record_queue_id) "
                "VALUES (?, ?, 'failed', ?, ?, ?)",
                (
                    finalization_id,
                    expired_turn_id,
                    _json(payload),
                    assistant_message_id,
                    record_queue_id,
                ),
            )
            db.execute(
                "UPDATE agent_turns SET status = 'failed', "
                "terminal_outcome = 'failed', terminal_error = ?, "
                "outcome_payload = ?, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                "AND terminal_outcome = 'paused'",
                (_json(error), _json(payload), expired_turn_id),
            )
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_turn_events "
                "WHERE turn_id = ?",
                (expired_turn_id,),
            ).fetchone()
            db.execute(
                "INSERT INTO agent_turn_events "
                "(id, turn_id, sequence, event_type, phase, attempt, payload) "
                "VALUES (?, ?, ?, 'run_failed', 'pending_input', 0, ?)",
                (
                    _id(),
                    expired_turn_id,
                    int(sequence[0]),
                    _json(
                        {
                            **payload,
                            "pending_input_id": pending_id,
                            "finalization_id": finalization_id,
                            "outcome_kind": "failed",
                        }
                    ),
                ),
            )
        db.commit()
    except Exception:
        _rollback(db)
        raise


def _coerce_pending_value(value: object, schema: dict[str, Any]) -> object:
    expected = schema.get("type")
    if expected == "string":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("pending input requires a non-empty string")
        return value.strip()
    if expected == "integer":
        if isinstance(value, bool):
            raise ValueError("pending input requires an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("pending input requires an integer") from exc
        if isinstance(value, float) and value != parsed:
            raise ValueError("pending input requires an integer")
        return parsed
    if expected == "number":
        if isinstance(value, bool):
            raise ValueError("pending input requires a number")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("pending input requires a number") from exc
    if expected == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
            return value.strip().casefold() == "true"
        raise ValueError("pending input requires a boolean")
    raise ValueError("pending input schema type is unsupported")


def make_create_agent_pending_input_handler(db: DatabaseManager) -> OperationHandler:
    """Persist or replay one missing-input pause atomically."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreateAgentPendingInputParams)
        try:
            _begin(db)
            pending_confirmation = db.execute(
                "SELECT 1 FROM agent_confirmations WHERE turn_id = ? "
                "AND status = 'pending' LIMIT 1",
                (p.turn_id,),
            ).fetchone()
            if pending_confirmation is not None:
                raise ValueError("turn already has pending confirmations")
            pending_input = db.execute(
                "SELECT id FROM agent_pending_inputs WHERE turn_id = ? "
                "AND status = 'pending' LIMIT 1",
                (p.turn_id,),
            ).fetchone()
            if pending_input is not None and pending_input[0] != p.pending_input_id:
                raise ValueError("turn already has a pending input")
            db.execute(
                "INSERT OR IGNORE INTO agent_pending_inputs "
                "(id, turn_id, question, expected_input_schema, "
                "transcript_snapshot, expires_at, suggestions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    p.pending_input_id,
                    p.turn_id,
                    p.question,
                    _json(p.expected_input_schema),
                    _json(p.transcript_snapshot),
                    p.expires_at,
                    _json(p.suggestions),
                ),
            )
            row = db.execute(
                "SELECT p.id, p.turn_id, p.question, p.expected_input_schema, "
                "p.transcript_snapshot, p.status, p.cas_version, p.answer_payload, "
                "p.expires_at, p.suggestions "
                "FROM agent_pending_inputs p WHERE p.id = ?",
                (p.pending_input_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("pending input was not persisted")
            expected = (
                p.turn_id,
                p.question,
                _json(p.expected_input_schema),
                _json(p.transcript_snapshot),
                p.expires_at,
                _json(p.suggestions),
            )
            if (row[1], row[2], row[3], row[4], row[8], row[9]) != expected:
                raise ValueError("pending input replay payload mismatch")
            db.execute(
                "UPDATE agent_turns SET status = 'paused', "
                "terminal_outcome = 'paused', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                "AND terminal_outcome IS NULL",
                (p.turn_id,),
            )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        return {"pending_input": _pending_input_payload(row)}

    return handle


def make_get_agent_pending_input_handler(db: DatabaseManager) -> OperationHandler:
    """Read the canonical pending input, expiring stale state first."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetAgentPendingInputParams)
        if bool(p.pending_input_id) == bool(p.conversation_id):
            raise ValueError("provide exactly one pending input or conversation id")
        _expire_pending_inputs(db)
        if p.pending_input_id:
            row = db.execute(
                "SELECT p.id, p.turn_id, p.question, p.expected_input_schema, "
                "p.transcript_snapshot, p.status, p.cas_version, p.answer_payload, "
                "p.expires_at, p.suggestions FROM agent_pending_inputs p "
                "JOIN agent_turns t "
                "ON t.id = p.turn_id WHERE p.id = ? "
                "ORDER BY p.created_at DESC LIMIT 1",
                (p.pending_input_id,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT p.id, p.turn_id, p.question, p.expected_input_schema, "
                "p.transcript_snapshot, p.status, p.cas_version, p.answer_payload, "
                "p.expires_at, p.suggestions FROM agent_pending_inputs p "
                "JOIN agent_turns t "
                "ON t.id = p.turn_id WHERE t.conversation_id = ? "
                "AND p.status = 'pending' ORDER BY p.created_at DESC LIMIT 1",
                (p.conversation_id,),
            ).fetchone()
        return {"pending_input": _pending_input_payload(row) if row else None}

    return handle


def make_reply_agent_pending_input_handler(db: DatabaseManager) -> OperationHandler:
    """Validate and CAS a reply into the same structured run."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ReplyAgentPendingInputParams)
        try:
            _begin(db)
            row = db.execute(
                "SELECT p.id, p.turn_id, p.question, p.expected_input_schema, "
                "p.transcript_snapshot, p.status, p.cas_version, p.answer_payload, "
                "p.expires_at, p.suggestions "
                "FROM agent_pending_inputs p JOIN agent_turns t ON t.id = p.turn_id "
                "WHERE p.id = ? AND p.turn_id = ? AND t.conversation_id = ?",
                (p.pending_input_id, p.turn_id, p.conversation_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown pending input")
            if row[5] != "pending" or int(row[6]) != p.cas_version:
                raise ValueError("pending input is stale or already decided")
            if row[8] <= datetime.now(UTC).isoformat():
                _rollback(db)
                _expire_pending_inputs(db, turn_id=p.turn_id)
                expired = db.execute(
                    "SELECT id, turn_id, question, expected_input_schema, "
                    "transcript_snapshot, status, cas_version, answer_payload, "
                    "expires_at, suggestions FROM agent_pending_inputs WHERE id = ?",
                    (p.pending_input_id,),
                ).fetchone()
                return {"pending_input": _pending_input_payload(expired)}
            try:
                coerced = _coerce_pending_value(p.value, json.loads(row[3]))
            except ValueError as exc:
                db.commit()
                return {
                    "pending_input": _pending_input_payload(row),
                    "validation_error": str(exc),
                }
            cursor = db.execute(
                "UPDATE agent_pending_inputs SET status = 'answered', "
                "answer_payload = ?, "
                "answered_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
                "cas_version = cas_version + 1, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? AND turn_id = ? "
                "AND status = 'pending' AND cas_version = ?",
                (_json(coerced), p.pending_input_id, p.turn_id, p.cas_version),
            )
            if cursor.rowcount != 1:
                raise ValueError("pending input decision lost its CAS race")
            db.execute(
                "UPDATE agent_turns SET status = 'tool_loop', terminal_outcome = NULL, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ? "
                "AND terminal_outcome = 'paused'",
                (p.turn_id,),
            )
            db.commit()
        except Exception:
            _rollback(db)
            raise
        updated = db.execute(
            "SELECT id, turn_id, question, expected_input_schema, transcript_snapshot, "
            "status, cas_version, answer_payload, expires_at, suggestions "
            "FROM agent_pending_inputs "
            "WHERE id = ?",
            (p.pending_input_id,),
        ).fetchone()
        return {"pending_input": _pending_input_payload(updated)}

    return handle


__all__ = [name for name in globals() if name.startswith("make_")]
