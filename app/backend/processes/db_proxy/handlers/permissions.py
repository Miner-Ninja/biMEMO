"""DB proxy handlers for assistant project roots and capability grants."""

# ruff: noqa: S608

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

from backend.agent.turn_state import InvocationStatus, TurnLifecycleStatus
from backend.common import canonical_json
from backend.security.runtime_schema import prune_runtime_state

from ..common import OperationHandler, validate_params
from ..params import (
    AddProjectRootParams,
    AuthorizeAgentInvocationParams,
    CreateCapabilityGrantParams,
    ExpireCapabilityGrantsParams,
    GetAgentRunParams,
    ListAgentRunEventsParams,
    ListAgentRunsParams,
    ListCapabilityGrantsParams,
    ListPendingActionsParams,
    ListProjectRootsParams,
    LookupCapabilityGrantParams,
    ProjectAgentTurnEventParams,
    ProjectRootIdParams,
    PruneRuntimeStateParams,
    RenameProjectRootParams,
    ResolveProjectRootParams,
    RevokeCapabilityGrantParams,
)


def _new_id() -> str:
    """Return a new stable row identifier."""
    return str(uuid.uuid4())


def _begin(db: DatabaseManager) -> None:
    db.execute("BEGIN IMMEDIATE")


def _rollback(db: DatabaseManager) -> None:
    db.execute("ROLLBACK")


def _project_root_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one project_roots row into API shape."""
    return {
        "id": row[0],
        "name": row[1],
        "root_path": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


def _grant_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one capability_grants row into API shape."""
    return {
        "id": row[0],
        "scope_type": row[1],
        "scope_value": row[2],
        "capability": row[3],
        "decision": row[4],
        "channel": row[5],
        "scope_json": json.loads(row[6]),
        "scope_hash": row[7],
        "policy_revision": row[8],
        "origin_confirmation_id": row[9],
        "revoked_at": row[10],
        "expires_at": row[11],
        "created_at": row[12],
        "updated_at": row[13],
    }


_GRANT_COLUMNS = (
    "id, scope_type, scope_value, capability, decision, channel, scope_json, "
    "scope_hash, policy_revision, origin_confirmation_id, revoked_at, expires_at, "
    "created_at, updated_at"
)


def _json_dump(value: dict[str, Any]) -> str:
    """Serialize one JSON object for storage."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_load(value: str) -> dict[str, Any]:
    """Parse a stored JSON object, falling back to an empty object."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one agent_runs row into API shape."""
    return {
        "id": row[0],
        "conversation_id": row[1],
        "session_id": row[2],
        "channel": row[3],
        "status": row[4],
        "current_step": row[5],
        "request_id": row[6],
        "resume_payload": _json_load(row[7]),
        "tool_policy_version": row[8],
        "created_at": row[9],
        "updated_at": row[10],
        "completed_at": row[11],
    }


def _run_event_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one agent_run_events row into API shape."""
    return {
        "id": row[0],
        "run_id": row[1],
        "request_id": row[2],
        "conversation_id": row[3],
        "sequence": row[4],
        "event_type": row[5],
        "payload": _json_load(row[6]),
        "created_at": row[7],
        "delivered_at": row[8],
    }


def _pending_action_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one pending_actions row into API shape."""
    return {
        "id": row[0],
        "run_id": row[1],
        "tool_name": row[2],
        "parameters": _json_load(row[3]),
        "channel": row[4],
        "status": row[5],
        "decision": row[6],
        "preview": _json_load(row[7]),
        "result": _json_load(row[8]),
        "error_message": row[9],
        "created_at": row[10],
        "updated_at": row[11],
        "expires_at": row[12],
        "decided_at": row[13],
        "executed_at": row[14],
    }


def _resolve_root_path(path: str) -> str:
    """Normalize one project root path without requiring it to exist."""
    return str(Path(path).expanduser().resolve(strict=False))


def make_list_project_roots_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_project_roots operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        validate_params(params, ListProjectRootsParams)
        rows = db.execute(
            "SELECT id, name, root_path, created_at, updated_at "
            "FROM project_roots ORDER BY name COLLATE NOCASE, root_path"
        ).fetchall()
        return {"project_roots": [_project_root_record(row) for row in rows]}

    return handle


def make_add_project_root_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for add_project_root operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, AddProjectRootParams)
        root_id = _new_id()
        root_path = _resolve_root_path(p.root_path)
        db.execute(
            "INSERT INTO project_roots (id, name, root_path) VALUES (?, ?, ?) "
            "ON CONFLICT(root_path) DO UPDATE SET "
            "name = excluded.name, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')",
            (root_id, p.name, root_path),
        )
        db.commit()
        row = db.execute(
            "SELECT id, name, root_path, created_at, updated_at "
            "FROM project_roots WHERE root_path = ?",
            (root_path,),
        ).fetchone()
        return {"project_root": _project_root_record(row)}

    return handle


def make_remove_project_root_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for remove_project_root operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ProjectRootIdParams)
        row = db.execute(
            "SELECT root_path FROM project_roots WHERE id = ?",
            (p.id,),
        ).fetchone()
        cursor = db.execute("DELETE FROM project_roots WHERE id = ?", (p.id,))
        if row is not None:
            db.execute(
                "DELETE FROM capability_grants "
                "WHERE scope_type = 'project_root' AND scope_value = ?",
                (row[0],),
            )
        db.commit()
        return {"removed": cursor.rowcount > 0}

    return handle


def make_rename_project_root_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for rename_project_root operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, RenameProjectRootParams)
        db.execute(
            "UPDATE project_roots SET name = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
            (p.name, p.id),
        )
        db.commit()
        row = db.execute(
            "SELECT id, name, root_path, created_at, updated_at "
            "FROM project_roots WHERE id = ?",
            (p.id,),
        ).fetchone()
        return {"project_root": _project_root_record(row) if row else None}

    return handle


def make_resolve_project_root_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for resolve_project_root operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ResolveProjectRootParams)
        row = db.execute(
            "SELECT id, name, root_path, created_at, updated_at "
            "FROM project_roots WHERE id = ?",
            (p.id,),
        ).fetchone()
        return {"project_root": _project_root_record(row) if row else None}

    return handle


def make_create_capability_grant_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for create_capability_grant operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreateCapabilityGrantParams)
        grant_id = _new_id()
        db.execute(
            "INSERT INTO capability_grants "
            "(id, scope_type, scope_value, capability, decision, channel, scope_json, "
            "scope_hash, policy_revision, origin_confirmation_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_type, scope_value, capability, channel) DO UPDATE SET "
            "decision = excluded.decision, scope_json = excluded.scope_json, "
            "scope_hash = excluded.scope_hash, "
            "policy_revision = excluded.policy_revision, "
            "origin_confirmation_id = excluded.origin_confirmation_id, "
            "revoked_at = NULL, expires_at = excluded.expires_at, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')",
            (
                grant_id,
                p.scope_type,
                p.scope_value,
                p.capability,
                p.decision,
                p.channel,
                json.dumps(p.scope_json, ensure_ascii=False, sort_keys=True),
                p.scope_hash,
                p.policy_revision,
                p.origin_confirmation_id,
                p.expires_at,
            ),
        )
        db.commit()
        row = db.execute(
            f"SELECT {_GRANT_COLUMNS} FROM capability_grants "
            "WHERE scope_type = ? AND scope_value = ? AND capability = ? "
            "AND channel = ?",
            (p.scope_type, p.scope_value, p.capability, p.channel),
        ).fetchone()
        return {"grant": _grant_record(row)}

    return handle


def make_list_capability_grants_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_capability_grants operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListCapabilityGrantsParams)
        clauses: list[str] = []
        values: list[Any] = []
        for column in ("scope_type", "scope_value", "capability", "channel"):
            value = getattr(p, column)
            if value is None:
                continue
            clauses.append(f"{column} = ?")
            values.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            f"SELECT {_GRANT_COLUMNS} FROM capability_grants {where} "
            "ORDER BY created_at DESC",
            tuple(values),
        ).fetchall()
        return {"grants": [_grant_record(row) for row in rows]}

    return handle


def make_lookup_capability_grant_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for lookup_capability_grant operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, LookupCapabilityGrantParams)
        row = db.execute(
            f"SELECT {_GRANT_COLUMNS} FROM capability_grants "
            "WHERE scope_type = ? AND scope_value = ? AND capability = ? "
            "AND channel = ? "
            "AND (expires_at IS NULL OR expires_at > "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now')) "
            "AND revoked_at IS NULL AND scope_hash = ? AND policy_revision = ? "
            "ORDER BY created_at DESC "
            "LIMIT 1",
            (
                p.scope_type,
                p.scope_value,
                p.capability,
                p.channel,
                p.scope_hash,
                p.policy_revision,
            ),
        ).fetchone()
        return {"grant": _grant_record(row) if row else None}

    return handle


def make_authorize_agent_invocation_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Atomically validate/create a scoped grant and claim an invocation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, AuthorizeAgentInvocationParams)
        try:
            _begin(db)
            invocation = db.execute(
                "SELECT i.status, i.claim_generation, i.idempotency_key, "
                "i.tool_name, t.conversation_id, t.terminal_outcome, t.channel, "
                "i.canonical_args_hash, i.policy_revision "
                "FROM agent_tool_invocations i JOIN agent_turns t "
                "ON t.id = i.turn_id WHERE i.id = ? AND i.turn_id = ?",
                (p.invocation_id, p.turn_id),
            ).fetchone()
            if invocation is None:
                raise ValueError("unknown invocation")
            if invocation[3] != p.capability:
                raise PermissionError("grant capability does not match invocation")
            if invocation[7] != p.canonical_args_hash:
                raise PermissionError("authorization arguments do not match invocation")
            if invocation[8] != p.policy_revision:
                raise PermissionError("authorization policy revision mismatch")
            if invocation[5] not in (None, "paused"):
                raise RuntimeError("terminal turn cannot dispatch another invocation")
            canonical_scope = canonical_json(p.scope_json)
            canonical_hash = hashlib.sha256(canonical_scope.encode("utf-8")).hexdigest()
            if canonical_hash != p.scope_hash:
                raise PermissionError("grant scope hash does not match scope payload")
            if p.scope_json.get("conversation_id") != invocation[4]:
                raise PermissionError("grant belongs to another conversation")
            if p.channel != invocation[6]:
                raise PermissionError("grant channel does not match invocation")
            if p.scope_type == "conversation_capability":
                if p.scope_value != invocation[4]:
                    raise PermissionError("conversation grant scope is invalid")
            elif not p.scope_value.startswith(f"{invocation[4]}:"):
                raise PermissionError("scoped grant belongs to another conversation")
            if invocation[0] == "executing" and invocation[2] == p.idempotency_key:
                db.commit()
                return {
                    "claimed": False,
                    "status": "executing",
                    "claim_generation": invocation[1],
                }
            expected_status = (
                InvocationStatus.PENDING_CONFIRMATION
                if p.confirmation_id is not None
                else InvocationStatus.REQUIRED
            )
            if invocation[0] != expected_status:
                raise RuntimeError(f"invocation cannot be claimed from {invocation[0]}")

            grant_id: str | None = None
            if p.confirmation_id is not None:
                confirmation = db.execute(
                    "SELECT status, invocation_id, expires_at, "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                    "FROM agent_confirmations WHERE id = ? AND turn_id = ?",
                    (p.confirmation_id, p.turn_id),
                ).fetchone()
                if confirmation is None or confirmation[1] != p.invocation_id:
                    raise PermissionError("confirmation does not match invocation")
                if confirmation[0] not in {"pending", "approved"}:
                    raise RuntimeError("confirmation is no longer authorizable")
                if confirmation[0] == "pending" and str(confirmation[2]) <= str(
                    confirmation[3]
                ):
                    raise RuntimeError("confirmation expired before authorization")
                if confirmation[0] == "pending":
                    db.execute(
                        "UPDATE agent_confirmations SET status = 'approved', "
                        "decided_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                        "WHERE id = ?",
                        (p.confirmation_id,),
                    )
                if p.create_reusable_grant:
                    if p.grant_expires_at is None:
                        raise ValueError("reusable grant requires an expiration")
                    try:
                        grant_expiry = datetime.fromisoformat(p.grant_expires_at)
                    except ValueError as exc:
                        raise ValueError(
                            "reusable grant expiration is invalid"
                        ) from exc
                    if grant_expiry.tzinfo is None or grant_expiry <= datetime.now(UTC):
                        raise ValueError(
                            "reusable grant expiration must be in the future"
                        )
                    canonical_grant_expiry = grant_expiry.astimezone(UTC).isoformat()
                    grant_id = _new_id()
                    db.execute(
                        "INSERT INTO capability_grants "
                        "(id, scope_type, scope_value, capability, decision, channel, "
                        "scope_json, scope_hash, policy_revision, "
                        "origin_confirmation_id, expires_at) "
                        "VALUES (?, ?, ?, ?, 'allow', ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(scope_type, scope_value, capability, channel) "
                        "DO UPDATE SET decision = 'allow', "
                        "scope_json = excluded.scope_json, "
                        "scope_hash = excluded.scope_hash, "
                        "policy_revision = excluded.policy_revision, "
                        "origin_confirmation_id = excluded.origin_confirmation_id, "
                        "expires_at = excluded.expires_at, "
                        "revoked_at = NULL, updated_at = "
                        "strftime('%Y-%m-%dT%H:%M:%f', 'now')",
                        (
                            grant_id,
                            p.scope_type,
                            p.scope_value,
                            p.capability,
                            p.channel,
                            json.dumps(
                                p.scope_json,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            p.scope_hash,
                            p.policy_revision,
                            p.confirmation_id,
                            canonical_grant_expiry,
                        ),
                    )
            else:
                grant = db.execute(
                    "SELECT id FROM capability_grants WHERE scope_type = ? "
                    "AND scope_value = ? AND capability = ? AND channel = ? "
                    "AND decision = 'allow' AND revoked_at IS NULL "
                    "AND scope_hash = ? AND policy_revision = ? "
                    "AND expires_at IS NOT NULL AND expires_at > "
                    "strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (
                        p.scope_type,
                        p.scope_value,
                        p.capability,
                        p.channel,
                        p.scope_hash,
                        p.policy_revision,
                    ),
                ).fetchone()
                if grant is None:
                    raise PermissionError("no active compatible capability grant")
                grant_id = str(grant[0])

            generation = int(invocation[1]) + 1
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
        return {
            "claimed": True,
            "status": "executing",
            "claim_generation": generation,
            "grant_id": grant_id,
        }

    return handle


def make_revoke_capability_grant_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for revoke_capability_grant operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, RevokeCapabilityGrantParams)
        cursor = db.execute(
            "UPDATE capability_grants SET revoked_at = "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ? AND revoked_at IS NULL",
            (p.id,),
        )
        db.commit()
        return {"revoked": cursor.rowcount > 0}

    return handle


def make_expire_capability_grants_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for expire_capability_grants operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ExpireCapabilityGrantsParams)
        if p.now:
            cursor = db.execute(
                "DELETE FROM capability_grants WHERE expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (p.now,),
            )
        else:
            cursor = db.execute(
                "DELETE FROM capability_grants WHERE expires_at IS NOT NULL "
                "AND expires_at <= strftime('%Y-%m-%dT%H:%M:%f', 'now')"
            )
        db.commit()
        return {"expired": cursor.rowcount}

    return handle


def make_project_agent_turn_event_handler(db: DatabaseManager) -> OperationHandler:
    """Create the idempotent runtime projection write handler."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ProjectAgentTurnEventParams)
        db.execute(
            "INSERT OR IGNORE INTO agent_runs "
            "(id, conversation_id, session_id, channel, status, current_step, "
            "request_id, resume_payload) VALUES (?, ?, ?, ?, 'projection', "
            "'core_projection', '', '{}')",
            (p.turn_id, p.conversation_id, p.session_id, p.channel),
        )
        # Mirror the turn's lifecycle onto the projected run row so the
        # trace list shows real states instead of a constant 'projection'.
        projected_status = {
            "run_started": TurnLifecycleStatus.RUNNING,
            "run_paused": TurnLifecycleStatus.PAUSED,
            "final_accepted": TurnLifecycleStatus.FINAL,
            "run_failed": TurnLifecycleStatus.FAILED,
            "run_cancelled": TurnLifecycleStatus.CANCELLED,
        }.get(p.event_type)
        if projected_status is not None:
            db.execute(
                "UPDATE agent_runs SET status = ? WHERE id = ?",
                (projected_status, p.turn_id),
            )
        cursor = db.execute(
            "INSERT OR IGNORE INTO agent_run_events "
            "(id, run_id, request_id, conversation_id, sequence, event_type, "
            "payload_json, created_at) VALUES (?, ?, '', ?, ?, ?, ?, ?)",
            (
                p.event_id,
                p.turn_id,
                p.conversation_id,
                p.sequence,
                p.event_type,
                _json_dump(p.payload),
                p.created_at,
            ),
        )
        db.commit()
        return {"projected": cursor.rowcount == 1}

    return handle


def make_list_agent_run_events_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_agent_run_events operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListAgentRunEventsParams)
        filters = ["sequence > ?"]
        values: list[Any] = [p.after_sequence]
        if p.run_id:
            filters.append("run_id = ?")
            values.append(p.run_id)
        if p.request_id:
            filters.append("request_id = ?")
            values.append(p.request_id)
        if p.conversation_id:
            filters.append("conversation_id = ?")
            values.append(p.conversation_id)
        values.append(p.limit)
        rows = db.execute(
            "SELECT id, run_id, request_id, conversation_id, sequence, event_type, "
            "payload_json, created_at, delivered_at FROM agent_run_events "
            f"WHERE {' AND '.join(filters)} "
            "ORDER BY created_at ASC, sequence ASC LIMIT ?",
            tuple(values),
        ).fetchall()
        return {"events": [_run_event_record(row) for row in rows]}

    return handle


def make_list_agent_runs_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_agent_runs operation (recent runs, newest first)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListAgentRunsParams)
        clauses: list[str] = []
        values: list[Any] = []
        if p.conversation_id:
            clauses.append("conversation_id = ?")
            values.append(p.conversation_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend([p.limit, p.offset])
        rows = db.execute(
            "SELECT id, conversation_id, session_id, channel, status, current_step, "
            "request_id, resume_payload, tool_policy_version, created_at, "
            "updated_at, completed_at "
            f"FROM agent_runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(values),
        ).fetchall()
        return {"runs": [_run_record(row) for row in rows]}

    return handle


def make_get_agent_run_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_agent_run operation (single run by id)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetAgentRunParams)
        row = db.execute(
            "SELECT id, conversation_id, session_id, channel, status, current_step, "
            "request_id, resume_payload, tool_policy_version, created_at, "
            "updated_at, completed_at "
            "FROM agent_runs WHERE id = ?",
            (p.run_id,),
        ).fetchone()
        return {"agent_run": _run_record(row) if row is not None else None}

    return handle


def make_list_pending_actions_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_pending_actions operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ListPendingActionsParams)
        clauses: list[str] = []
        values: list[Any] = []
        for column in ("run_id", "status", "channel"):
            value = getattr(p, column)
            if value is None:
                continue
            clauses.append(f"{column} = ?")
            values.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            "SELECT id, run_id, tool_name, parameters_json, channel, status, "
            "decision, preview_json, result_json, error_message, created_at, "
            f"updated_at, expires_at, decided_at, executed_at "
            f"FROM pending_actions {where} ORDER BY created_at DESC",
            tuple(values),
        ).fetchall()
        return {"pending_actions": [_pending_action_record(row) for row in rows]}

    return handle


def make_prune_runtime_state_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for prune_runtime_state operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, PruneRuntimeStateParams)
        return prune_runtime_state(
            db,
            now=p.now,
            snapshot_retention_hours=p.snapshot_retention_hours,
            run_retention_days=p.run_retention_days,
        )

    return handle
