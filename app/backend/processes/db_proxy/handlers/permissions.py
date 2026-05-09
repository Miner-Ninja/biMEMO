"""DB proxy handlers for assistant project roots and capability grants."""

# ruff: noqa: S608

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

from backend.security.runtime_schema import prune_runtime_state

from ..common import OperationHandler, validate_params
from ..params import (
    AddProjectRootParams,
    CompletePendingActionParams,
    CreateAgentRunParams,
    CreateCapabilityGrantParams,
    CreatePendingActionParams,
    DecidePendingActionParams,
    ExpireCapabilityGrantsParams,
    ExpirePendingActionsParams,
    GetAgentRunParams,
    GetPendingActionParams,
    GetResumeSnapshotParams,
    ListCapabilityGrantsParams,
    ListPendingActionsParams,
    ListProjectRootsParams,
    LookupCapabilityGrantParams,
    ProjectRootIdParams,
    PruneRuntimeStateParams,
    RenameProjectRootParams,
    ResolveProjectRootParams,
    RevokeCapabilityGrantParams,
    UpdateAgentRunStatusParams,
)


_RUN_TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped"})

def _new_id() -> str:
    """Return a new stable row identifier."""
    return str(uuid.uuid4())


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
        "expires_at": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }


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
        "created_at": row[8],
        "updated_at": row[9],
        "completed_at": row[10],
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


def _resume_snapshot_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one resume_snapshots row into API shape."""
    return {
        "id": row[0],
        "run_id": row[1],
        "action_id": row[2],
        "snapshot": _json_load(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
        "expires_at": row[6],
        "consumed_at": row[7],
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
            "(id, scope_type, scope_value, capability, decision, channel, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_type, scope_value, capability, channel) DO UPDATE SET "
            "decision = excluded.decision, expires_at = excluded.expires_at, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')",
            (
                grant_id,
                p.scope_type,
                p.scope_value,
                p.capability,
                p.decision,
                p.channel,
                p.expires_at,
            ),
        )
        db.commit()
        row = db.execute(
            "SELECT id, scope_type, scope_value, capability, decision, channel, "
            "expires_at, created_at, updated_at FROM capability_grants "
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
            "SELECT id, scope_type, scope_value, capability, decision, channel, "
            f"expires_at, created_at, updated_at FROM capability_grants {where} "
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
            "SELECT id, scope_type, scope_value, capability, decision, channel, "
            "expires_at, created_at, updated_at FROM capability_grants "
            "WHERE scope_type = ? AND scope_value = ? AND capability = ? "
            "AND channel IN (?, '') "
            "AND (expires_at IS NULL OR expires_at > "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now')) "
            "ORDER BY CASE WHEN channel = ? THEN 0 ELSE 1 END, created_at DESC "
            "LIMIT 1",
            (p.scope_type, p.scope_value, p.capability, p.channel, p.channel),
        ).fetchone()
        return {"grant": _grant_record(row) if row else None}

    return handle


def make_revoke_capability_grant_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for revoke_capability_grant operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, RevokeCapabilityGrantParams)
        cursor = db.execute("DELETE FROM capability_grants WHERE id = ?", (p.id,))
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


def make_create_agent_run_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for create_agent_run operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreateAgentRunParams)
        run_id = _new_id()
        completed_expr = (
            "strftime('%Y-%m-%dT%H:%M:%f', 'now')"
            if p.status in _RUN_TERMINAL_STATUSES
            else "NULL"
        )
        db.execute(
            "INSERT INTO agent_runs "
            "(id, conversation_id, session_id, channel, status, current_step, "
            "request_id, resume_payload, completed_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, {completed_expr})",
            (
                run_id,
                p.conversation_id,
                p.session_id,
                p.channel,
                p.status,
                p.current_step,
                p.request_id,
                _json_dump(p.resume_payload),
            ),
        )
        db.commit()
        row = _fetch_agent_run(db, run_id)
        return {"agent_run": _run_record(row)}

    return handle


def make_get_agent_run_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_agent_run operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetAgentRunParams)
        row = _fetch_agent_run(db, p.run_id)
        return {"agent_run": _run_record(row) if row else None}

    return handle


def make_update_agent_run_status_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for update_agent_run_status operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, UpdateAgentRunStatusParams)
        completed_expr = (
            "strftime('%Y-%m-%dT%H:%M:%f', 'now')"
            if p.status in _RUN_TERMINAL_STATUSES
            else "NULL"
        )
        values: list[Any] = [p.status, p.current_step]
        resume_sql = ""
        if p.resume_payload is not None:
            resume_sql = ", resume_payload = ?"
            values.append(_json_dump(p.resume_payload))
        values.append(p.run_id)
        db.execute(
            "UPDATE agent_runs SET status = ?, current_step = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
            f"completed_at = {completed_expr}{resume_sql} WHERE id = ?",
            tuple(values),
        )
        db.commit()
        row = _fetch_agent_run(db, p.run_id)
        return {"agent_run": _run_record(row) if row else None}

    return handle


def make_create_pending_action_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for create_pending_action operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreatePendingActionParams)
        action_id = p.action_id or _new_id()
        snapshot_id = _new_id() if p.resume_snapshot else ""
        preview = dict(p.preview)
        if snapshot_id:
            preview["resume_snapshot_id"] = snapshot_id
        db.execute(
            "INSERT INTO pending_actions "
            "(id, run_id, tool_name, parameters_json, channel, status, preview_json, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                p.run_id,
                p.tool_name,
                _json_dump(p.parameters),
                p.channel,
                p.status,
                _json_dump(preview),
                p.expires_at,
            ),
        )
        if snapshot_id:
            db.execute(
                "INSERT INTO resume_snapshots "
                "(id, run_id, action_id, snapshot_json, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    p.run_id,
                    action_id,
                    _json_dump(p.resume_snapshot),
                    p.expires_at,
                ),
            )
        db.execute(
            "UPDATE agent_runs SET status = 'waiting', "
            "current_step = 'pending_action', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
            (p.run_id,),
        )
        db.commit()
        row = _fetch_pending_action(db, action_id)
        return {"pending_action": _pending_action_record(row)}

    return handle


def make_get_pending_action_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_pending_action operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetPendingActionParams)
        row = _fetch_pending_action(db, p.action_id)
        return {"pending_action": _pending_action_record(row) if row else None}

    return handle


def make_get_resume_snapshot_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_resume_snapshot operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetResumeSnapshotParams)
        row = _fetch_resume_snapshot(
            db,
            action_id=p.action_id,
            snapshot_id=p.snapshot_id,
        )
        return {"resume_snapshot": _resume_snapshot_record(row) if row else None}

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


def make_decide_pending_action_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for decide_pending_action operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, DecidePendingActionParams)
        status = "approved" if p.decision == "approve" else "denied"
        cursor = db.execute(
            "UPDATE pending_actions SET status = ?, decision = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
            "decided_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ? AND status = 'waiting' "
            "AND (? = '' OR channel = ?)",
            (status, p.decision, p.action_id, p.channel, p.channel),
        )
        row = _fetch_pending_action(db, p.action_id)
        accepted = cursor.rowcount > 0
        if row is not None and accepted:
            run_status = "approved" if status == "approved" else "denied"
            completed_sql = (
                ", completed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')"
                if run_status == "denied"
                else ""
            )
            db.execute(
                "UPDATE agent_runs SET status = ?, current_step = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')"
                f"{completed_sql} WHERE id = ?",
                (run_status, "pending_action_decided", row[1]),
            )
            if run_status == "denied":
                db.execute(
                    "UPDATE resume_snapshots SET "
                    "consumed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                    "WHERE action_id = ? AND consumed_at IS NULL",
                    (p.action_id,),
                )
        db.commit()
        row = _fetch_pending_action(db, p.action_id)
        return {
            "accepted": accepted,
            "pending_action": _pending_action_record(row) if row else None,
        }

    return handle


def make_complete_pending_action_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for complete_pending_action operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CompletePendingActionParams)
        cursor = db.execute(
            "UPDATE pending_actions SET status = ?, result_json = ?, "
            "error_message = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
            "executed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ? AND status = 'approved'",
            (p.status, _json_dump(p.result), p.error_message, p.action_id),
        )
        row = _fetch_pending_action(db, p.action_id)
        accepted = cursor.rowcount > 0
        if row is not None and accepted:
            run_status = "completed" if p.status == "completed" else "failed"
            db.execute(
                "UPDATE resume_snapshots SET "
                "consumed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE action_id = ? AND consumed_at IS NULL",
                (p.action_id,),
            )
            db.execute(
                "UPDATE agent_runs SET status = ?, current_step = ?, "
                "resume_payload = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
                "completed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (
                    run_status,
                    "approved_action_executed",
                    _json_dump({"pending_action_id": p.action_id}),
                    row[1],
                ),
            )
        db.commit()
        row = _fetch_pending_action(db, p.action_id)
        return {
            "accepted": accepted,
            "pending_action": _pending_action_record(row) if row else None,
        }

    return handle


def make_expire_pending_actions_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for expire_pending_actions operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, ExpirePendingActionsParams)
        if p.now:
            cursor = db.execute(
                "UPDATE pending_actions SET status = 'expired', "
                "decision = 'expired', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
                "decided_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE status = 'waiting' AND expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (p.now,),
            )
        else:
            cursor = db.execute(
                "UPDATE pending_actions SET status = 'expired', decision = 'expired', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'), "
                "decided_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE status = 'waiting' AND expires_at IS NOT NULL "
                "AND expires_at <= strftime('%Y-%m-%dT%H:%M:%f', 'now')"
            )
        db.commit()
        return {"expired": cursor.rowcount}

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


def _fetch_agent_run(db: DatabaseManager, run_id: str) -> tuple[Any, ...] | None:
    """Fetch one agent run row by id."""
    return db.execute(
        "SELECT id, conversation_id, session_id, channel, status, current_step, "
        "request_id, resume_payload, created_at, updated_at, completed_at "
        "FROM agent_runs WHERE id = ?",
        (run_id,),
    ).fetchone()


def _fetch_pending_action(
    db: DatabaseManager,
    action_id: str,
) -> tuple[Any, ...] | None:
    """Fetch one pending action row by id."""
    return db.execute(
        "SELECT id, run_id, tool_name, parameters_json, channel, status, decision, "
        "preview_json, result_json, error_message, created_at, updated_at, "
        "expires_at, decided_at, executed_at "
        "FROM pending_actions WHERE id = ?",
        (action_id,),
    ).fetchone()


def _fetch_resume_snapshot(
    db: DatabaseManager,
    *,
    action_id: str = "",
    snapshot_id: str = "",
) -> tuple[Any, ...] | None:
    """Fetch one resume snapshot by id or by its pending action."""
    if snapshot_id:
        return db.execute(
            "SELECT id, run_id, action_id, snapshot_json, created_at, updated_at, "
            "expires_at, consumed_at FROM resume_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
    if action_id:
        return db.execute(
            "SELECT id, run_id, action_id, snapshot_json, created_at, updated_at, "
            "expires_at, consumed_at FROM resume_snapshots WHERE action_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (action_id,),
        ).fetchone()
    return None
