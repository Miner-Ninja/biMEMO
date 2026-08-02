"""Admin, auth, profile, rate-limit, and debug DB proxy handler factories."""

import json
import os
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..common import (
    JWT_CONTEXT_LABEL,
    OperationHandler,
    count_rows_for_table,
    parse_content,
    validate_params,
)
from ..params import (
    CountMemoryEntriesParams,
    DeleteAvatarParams,
    GetAvatarParams,
    GetRecentRateLimitCallsParams,
    ListAuditLogParams,
    ListMessagesParams,
    ListRecordsParams,
    ListSummariesFullParams,
    RecordRateLimitCallParams,
    StoreAvatarParams,
    StorePasswordHashParams,
    StoreUserProfileParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


RATE_LIMIT_RETENTION_SECONDS = 120.0

MEMORY_COUNT_QUERIES = {
    "messages": "SELECT count(*) FROM messages",
    "records": "SELECT count(*) FROM records",
    "summaries": "SELECT count(*) FROM summaries",
}

STATS_COUNT_QUERIES = {
    "messages": "SELECT count(*) FROM messages",
    "conversations": "SELECT count(*) FROM conversations",
    "records": "SELECT count(*) FROM records",
    "summaries": "SELECT count(*) FROM summaries",
    "reminders": "SELECT count(*) FROM reminders",
    "audit_log": "SELECT count(*) FROM audit_log",
    "integrations": "SELECT count(*) FROM integrations",
    "credentials": "SELECT count(*) FROM credentials",
}

LIST_RECORDS_SQL = (
    "SELECT "
    "id, summary, key_facts, action_items, conversation_id, created_at, "
    "tainted, source_message_ids, memory_kinds "
    "FROM records ORDER BY created_at DESC LIMIT ? OFFSET ?"
)

LIST_MESSAGES_SQL = (
    "SELECT id, role, content, conversation_id, created_at, meta "
    "FROM messages ORDER BY created_at DESC LIMIT ? OFFSET ?"
)

LIST_AUDIT_LOG_SQL = (
    "SELECT id, timestamp, event_type, tool_name, "
    "parameters, result, zone, duration_ms "
    "FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?"
)


def _table_counts(
    db: DatabaseManager,
    count_queries: dict[str, str],
) -> dict[str, int]:
    """Collect row counts for one set of tables."""
    counts: dict[str, int] = {}
    for table in count_queries:
        try:
            counts[table] = count_rows_for_table(db, table, count_queries)
        except Exception:
            counts[table] = 0
    return counts


def _json_list_cell(value: object) -> list[object]:
    """Decode one optional JSON list cell, falling back to an empty list."""
    return json.loads(value) if value else []


def _record_entry(row: tuple[object, ...]) -> dict[str, object]:
    """Build one record listing entry from a DB row."""
    return {
        "id": row[0],
        "summary": row[1],
        "key_facts": _json_list_cell(row[2]),
        "action_items": _json_list_cell(row[3]),
        "conversation_id": row[4],
        "created_at": row[5],
        "tainted": bool(row[6]),
        "source_message_ids": _json_list_cell(row[7]),
        "memory_kinds": _json_list_cell(row[8]),
    }


def _summary_full_entry(row: dict[str, object]) -> dict[str, object]:
    """Build one full summary listing entry."""
    return {
        "id": row["id"],
        "main_point": row["main_point"],
        "based_on": row["based_on"],
        "trust_level": row["trust_level"],
        "scope": row["scope"],
        "created_at": row["created_at"],
        "record_ids": row["source_record_ids"],
        "record_relations": row["record_relations"],
        "effective_trust_level_score": row["effective_trust_level_score"],
        "revalidation_status": row["revalidation_status"],
        "active_support_count": row["active_support_count"],
        "supporting_record_count": row["supporting_record_count"],
    }


def _message_entry(row: tuple[object, ...]) -> dict[str, object]:
    """Build one message listing entry from a DB row."""
    return {
        "id": row[0],
        "role": row[1],
        "content": parse_content(row[2]),
        "conversation_id": row[3],
        "created_at": row[4],
        "meta": row[5],
    }


def _audit_log_entry(row: tuple[object, ...]) -> dict[str, object]:
    """Build one audit log listing entry from a DB row."""
    return {
        "id": row[0],
        "timestamp": row[1],
        "event_type": row[2],
        "tool_name": row[3],
        "parameters": row[4],
        "result": row[5],
        "zone": row[6],
        "duration_ms": row[7],
    }


def _metadata_value(db: DatabaseManager, key: str) -> object | None:
    """Load one metadata value by key."""
    row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return row[0]


def _store_metadata_value(db: DatabaseManager, key: str, value: str) -> None:
    """Store one metadata value by key."""
    db.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (key, value),
    )
    db.commit()


def _json_metadata_profile(raw_value: object) -> dict[str, object]:
    """Decode one metadata JSON profile value safely."""
    if raw_value is None:
        return {}
    try:
        profile = json.loads(raw_value)
    except json.JSONDecodeError, TypeError:
        return {}
    return profile if isinstance(profile, dict) else {}


def _signing_salt(db: DatabaseManager) -> bytes:
    """Load or create the JWT signing salt."""
    stored_salt = _metadata_value(db, "jwt_salt")
    if isinstance(stored_salt, bytes):
        return stored_salt
    salt = os.urandom(16)
    db.execute(
        "INSERT INTO metadata (key, value) VALUES ('jwt_salt', ?)",
        (salt,),
    )
    db.commit()
    return salt


def _messages_query(
    conversation_id: str | None,
) -> tuple[str, tuple[object, ...]]:
    """Build the messages listing query and parameters."""
    if conversation_id:
        return (
            "SELECT id, role, content, conversation_id, created_at, meta "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (conversation_id,),
        )
    return (LIST_MESSAGES_SQL, ())


def _audit_log_query(
    event_type: str | None,
) -> tuple[str, tuple[object, ...]]:
    """Build the audit-log listing query and base parameters."""
    if event_type:
        return (
            "SELECT id, timestamp, event_type, tool_name, "
            "parameters, result, zone, duration_ms "
            "FROM audit_log WHERE event_type = ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (event_type,),
        )
    return (LIST_AUDIT_LOG_SQL, ())


def make_record_rate_limit_call_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for record_rate_limit_call operation."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS rate_limit_calls ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "tool_name TEXT NOT NULL, "
        "called_at REAL NOT NULL"
        ")"
    )

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Record a rate-limit call timestamp."""
        p = validate_params(params, RecordRateLimitCallParams)
        db.execute(
            "INSERT INTO rate_limit_calls (tool_name, called_at) VALUES (?, ?)",
            (p.tool_name, p.called_at),
        )
        db.execute(
            "DELETE FROM rate_limit_calls WHERE called_at < ?",
            (p.called_at - RATE_LIMIT_RETENTION_SECONDS,),
        )
        return {"ok": True}

    return handle


def make_get_recent_rate_limit_calls_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_recent_rate_limit_calls operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return recent call timestamps for a tool."""
        p = validate_params(params, GetRecentRateLimitCallsParams)
        rows = db.execute(
            "SELECT called_at FROM rate_limit_calls "
            "WHERE tool_name = ? AND called_at >= ? ORDER BY called_at",
            (p.tool_name, p.since),
        ).fetchall()
        return {"timestamps": [row[0] for row in rows]}

    return handle


def make_get_stats_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_stats operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Collect database statistics."""
        return _table_counts(db, STATS_COUNT_QUERIES)

    return handle


def make_create_backup_handler(
    db: DatabaseManager,
    database_path: str,
) -> OperationHandler:
    """Create handler for create_backup operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create a database backup file."""
        backup_dir = os.path.dirname(database_path)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"backup_{timestamp}.db")
        shutil.copy2(database_path, backup_path)
        return {"backup_path": backup_path}

    return handle


def make_get_signing_material_handler(
    db: DatabaseManager,
    master_key: bytes,
) -> OperationHandler:
    """Create handler for get_signing_material operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return HKDF-derived signing key material."""
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        salt = _signing_salt(db)
        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=salt,
            info=JWT_CONTEXT_LABEL,
        )
        key_material = hkdf.derive(master_key)
        return {
            "key_material": key_material.hex(),
            "salt": salt.hex(),
        }

    return handle


def make_store_password_hash_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_password_hash operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store the single-user password hash."""
        p = validate_params(params, StorePasswordHashParams)
        existing = db.execute("SELECT id FROM user_auth LIMIT 1").fetchone()
        if existing is not None:
            return {"error": "password_already_exists"}
        db.execute(
            "INSERT INTO user_auth (id, password_hash) VALUES (1, ?)",
            (p.password_hash,),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_get_password_hash_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_password_hash operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return the stored password hash, if it exists."""
        row = db.execute("SELECT password_hash FROM user_auth WHERE id = 1").fetchone()
        if row is None:
            return {"exists": False, "password_hash": None}
        return {"exists": True, "password_hash": row[0]}

    return handle


def make_initialize_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for initialize operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Initialize all database schemas."""
        from backend.memory.retrieval.fulltext import create_fts_tables
        from backend.security.audit import initialize_audit_schema
        from backend.security.credential_schema import initialize_credential_schema
        from backend.security.schema import initialize_schema
        from backend.tools.handlers.reminder import ensure_table

        initialize_schema(db)
        initialize_audit_schema(db)
        initialize_credential_schema(db)
        create_fts_tables(db)
        ensure_table(db)
        return {"ok": True}

    return handle


def make_get_user_profile_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_user_profile operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Get the user profile from metadata."""
        return {"profile": _json_metadata_profile(_metadata_value(db, "user_profile"))}

    return handle


def make_store_user_profile_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_user_profile operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store the user profile in metadata."""
        p = validate_params(params, StoreUserProfileParams)
        profile = {
            "first_name": p.first_name,
            "last_name": p.last_name,
            "display_name": p.display_name,
            "assistant_name": p.assistant_name,
            "timezone": p.timezone,
            "language": p.language,
            "custom_instructions": p.custom_instructions[:2000],
        }
        _store_metadata_value(db, "user_profile", json.dumps(profile))
        return {"ok": True}

    return handle


# --- Avatar operations ---

ALLOWED_AVATAR_TYPES = {"user", "ai"}


def make_store_avatar_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_avatar operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store an avatar image in the avatars table."""
        import base64

        p = validate_params(params, StoreAvatarParams)
        if p.avatar_type not in ALLOWED_AVATAR_TYPES:
            msg = f"Invalid avatar type: {p.avatar_type}"
            raise ValueError(msg)
        data = base64.b64decode(p.data)
        db.execute(
            "INSERT OR REPLACE INTO avatars (type, data, mime_type) VALUES (?, ?, ?)",
            (p.avatar_type, data, p.mime_type),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_get_avatar_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_avatar operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Get an avatar image from the avatars table."""
        import base64

        p = validate_params(params, GetAvatarParams)
        if p.avatar_type not in ALLOWED_AVATAR_TYPES:
            msg = f"Invalid avatar type: {p.avatar_type}"
            raise ValueError(msg)
        row = db.execute(
            "SELECT data, mime_type FROM avatars WHERE type = ?",
            (p.avatar_type,),
        ).fetchone()
        if row is None:
            return {"avatar": None}
        return {
            "avatar": {
                "data": base64.b64encode(row[0]).decode("ascii"),
                "mime_type": row[1],
            },
        }

    return handle


def make_delete_avatar_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for delete_avatar operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete an avatar from the avatars table."""
        p = validate_params(params, DeleteAvatarParams)
        if p.avatar_type not in ALLOWED_AVATAR_TYPES:
            msg = f"Invalid avatar type: {p.avatar_type}"
            raise ValueError(msg)
        db.execute("DELETE FROM avatars WHERE type = ?", (p.avatar_type,))
        db.commit()
        return {"ok": True}

    return handle


def make_list_records_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_records operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List records with full metadata."""
        p = validate_params(params, ListRecordsParams)
        rows = db.execute(LIST_RECORDS_SQL, (p.limit, p.offset)).fetchall()
        return {"records": [_record_entry(row) for row in rows]}

    return handle


def make_list_summaries_full_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_summaries_full operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List summaries with full metadata."""
        p = validate_params(params, ListSummariesFullParams)
        from backend.memory.summary_distiller.store import (
            list_revalidated_distillations,
        )

        rows = list_revalidated_distillations(db, limit=p.limit, offset=p.offset)
        return {"summaries": [_summary_full_entry(row) for row in rows]}

    return handle


def make_list_messages_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_messages operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List messages with metadata."""
        p = validate_params(params, ListMessagesParams)
        query, base_params = _messages_query(p.conversation_id)
        rows = db.execute(query, (*base_params, p.limit, p.offset)).fetchall()
        return {"messages": [_message_entry(row) for row in rows]}

    return handle


def make_count_memory_entries_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for count_memory_entries operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Count messages, records, and summaries."""
        validate_params(params, CountMemoryEntriesParams)
        return _table_counts(db, MEMORY_COUNT_QUERIES)

    return handle


def make_list_audit_log_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_audit_log operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List audit log entries with an optional event-type filter."""
        p = validate_params(params, ListAuditLogParams)
        query, base_params = _audit_log_query(p.event_type)
        rows = db.execute(query, (*base_params, p.limit, p.offset)).fetchall()
        return {"entries": [_audit_log_entry(row) for row in rows]}

    return handle
