"""Admin, auth, profile, rate-limit, and debug DB proxy handler factories."""

import json
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.ipc.errors import SettingsBlobTooLargeError
from backend.ipc.models import (
    decode_message_page_cursor,
    encode_message_page_cursor,
)

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
    GetLoopHeartbeatsParams,
    GetRecentRateLimitCallsParams,
    GetUiSettingsParams,
    LimitOffsetParams,
    ListAuditLogParams,
    ListMessagePageParams,
    ListMessagesParams,
    ListRecordsParams,
    RecordLoopHeartbeatParams,
    RecordRateLimitCallParams,
    StoreAvatarParams,
    StorePasswordHashParams,
    StoreUiSettingsParams,
    StoreUserProfileParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


RATE_LIMIT_RETENTION_SECONDS = 120.0

logger = logging.getLogger(__name__)


MEMORY_COUNT_QUERIES = {
    "messages": "SELECT count(*) FROM messages",
    "records": "SELECT count(*) FROM records",
    "assertions": "SELECT count(*) FROM assertions",
}

STATS_COUNT_QUERIES = {
    "messages": "SELECT count(*) FROM messages",
    "conversations": "SELECT count(*) FROM conversations",
    "records": "SELECT count(*) FROM records",
    "assertions": "SELECT count(*) FROM assertions",
    "reminders": "SELECT count(*) FROM reminders",
    "audit_log": "SELECT count(*) FROM audit_log",
    "integrations": "SELECT count(*) FROM integrations",
    "credentials": "SELECT count(*) FROM credentials",
}

LIST_RECORDS_SQL = (
    "SELECT "
    "id, summary, key_facts, action_items, conversation_id, created_at, "
    "tainted, source_message_ids, memory_kinds "
    "FROM records "
    "ORDER BY created_at DESC LIMIT ? OFFSET ?"
)

LIST_MESSAGES_SQL = (
    "SELECT id, role, content, conversation_id, created_at, meta "
    "FROM messages "
    "ORDER BY created_at DESC LIMIT ? OFFSET ?"
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
    db.commit()

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Record a rate-limit call timestamp."""
        p = validate_params(params, RecordRateLimitCallParams)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO rate_limit_calls (tool_name, called_at) VALUES (?, ?)",
                (p.tool_name, p.called_at),
            )
            db.execute(
                "DELETE FROM rate_limit_calls WHERE called_at < ?",
                (p.called_at - RATE_LIMIT_RETENTION_SECONDS,),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
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


def perform_backup(
    databases: dict[str, DatabaseManager],
    database_path: str,
) -> dict[str, str]:
    """Write consistent encrypted copies of every open database.

    Uses ``VACUUM INTO`` on each open database, which writes a consistent,
    still-encrypted copy through the live connection — unlike a raw file
    copy of a WAL-mode database, which can capture a torn state.

    Args:
        databases: Open DatabaseManager instances keyed by short name
            (e.g. "core", "runtime", "diagnostics").
        database_path: Path of the core database file; backups land in a
            ``backups/`` directory next to it.

    Returns:
        Written backup file paths keyed by database short name.
    """
    backup_dir = os.path.join(os.path.dirname(database_path), "backups")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    backup_paths: dict[str, str] = {}
    for name, database in databases.items():
        backup_path = os.path.join(
            backup_dir,
            f"backup_{name}_{timestamp}.db",
        )
        database.execute("VACUUM INTO ?", (backup_path,))
        backup_paths[name] = backup_path
    return backup_paths


def prune_old_backups(
    databases: dict[str, DatabaseManager],
    database_path: str,
    keep: int,
) -> int:
    """Delete the oldest backup files beyond ``keep`` per database name.

    Args:
        databases: Open databases (their short names select the files).
        database_path: Path of the core database file (locates backups/).
        keep: Newest copies to retain per database name.

    Returns:
        Number of files deleted.
    """
    backup_dir = os.path.join(os.path.dirname(database_path), "backups")
    if not os.path.isdir(backup_dir):
        return 0
    deleted = 0
    for name in databases:
        prefix = f"backup_{name}_"
        files = sorted(
            entry
            for entry in os.listdir(backup_dir)
            if entry.startswith(prefix) and entry.endswith(".db")
        )
        for stale in files[:-keep] if keep > 0 else files:
            os.remove(os.path.join(backup_dir, stale))
            deleted += 1
    return deleted


def make_create_backup_handler(
    databases: dict[str, DatabaseManager],
    database_path: str,
) -> OperationHandler:
    """Create handler for create_backup operation.

    Args:
        databases: Open DatabaseManager instances keyed by short name.
        database_path: Path of the core database file.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Write consistent encrypted copies of every open database."""
        return {"backup_paths": perform_backup(databases, database_path)}

    return handle


# Loop names allowed to report liveness; bounds the metadata key space.
LOOP_HEARTBEAT_NAMES = {"pruning", "graph_extractor", "backup", "reembed"}
LOOP_HEARTBEAT_KEY_PREFIX = "loop_heartbeat_"


def make_record_loop_heartbeat_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for record_loop_heartbeat operation (Socket A).

    Background loops report a successful pass so the health view can
    show when each last worked instead of failures dying in the log.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store the current time under the loop's heartbeat key."""
        p = validate_params(params, RecordLoopHeartbeatParams)
        if p.loop_name not in LOOP_HEARTBEAT_NAMES:
            return {"ok": False, "note": f"Unknown loop: {p.loop_name}"}
        db.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (
                LOOP_HEARTBEAT_KEY_PREFIX + p.loop_name,
                datetime.now(tz=UTC).isoformat().encode(),
            ),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_get_loop_heartbeats_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_loop_heartbeats operation (Socket B)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return last-success timestamps of the background loops."""
        validate_params(params, GetLoopHeartbeatsParams)
        rows = db.execute(
            "SELECT key, value FROM metadata WHERE key LIKE ?",
            (LOOP_HEARTBEAT_KEY_PREFIX + "%",),
        ).fetchall()
        heartbeats = {
            row[0][len(LOOP_HEARTBEAT_KEY_PREFIX) :]: (
                row[1].decode() if isinstance(row[1], bytes) else str(row[1])
            )
            for row in rows
        }
        return {"heartbeats": heartbeats}

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


# Web-tier UI settings blob (theme, voice mode, command history, …).
UI_SETTINGS_MAX_BYTES = 16384


def make_get_ui_settings_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_ui_settings operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Read the UI settings blob from metadata."""
        validate_params(params, GetUiSettingsParams)
        raw = _metadata_value(db, "ui_settings")
        if not raw:
            return {"settings": {}}
        try:
            settings = json.loads(raw)
        except json.JSONDecodeError, TypeError:
            logger.warning("Stored UI settings are not valid JSON; returning empty")
            return {"settings": {}}
        if not isinstance(settings, dict):
            return {"settings": {}}
        return {"settings": settings}

    return handle


def make_store_ui_settings_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_ui_settings operation.

    Rejects blobs over UI_SETTINGS_MAX_BYTES with an explicit error so a
    runaway client cannot grow the metadata row without bound.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Replace the UI settings blob in metadata."""
        p = validate_params(params, StoreUiSettingsParams)
        encoded = json.dumps(p.settings, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > UI_SETTINGS_MAX_BYTES:
            raise SettingsBlobTooLargeError(
                f"UI settings blob exceeds {UI_SETTINGS_MAX_BYTES} bytes"
            )
        _store_metadata_value(db, "ui_settings", encoded)
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


def make_list_messages_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_messages operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List messages with metadata."""
        p = validate_params(params, ListMessagesParams)
        query, base_params = _messages_query(p.conversation_id)
        rows = db.execute(query, (*base_params, p.limit, p.offset)).fetchall()
        return {"messages": [_message_entry(row) for row in rows]}

    return handle


def make_list_message_page_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_message_page operation.

    Returns one cursor page of a conversation's messages, newest first at
    the DB level: without a cursor the newest ``limit`` rows; with a
    cursor only rows strictly older than the (created_at, rowid)
    boundary. Fetches ``limit + 1`` rows to detect ``has_more``; the
    ``next_cursor`` encodes the oldest row of the returned page.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return one cursor page of messages for one conversation."""
        p = validate_params(params, ListMessagePageParams)
        if p.cursor is None:
            query = (
                "SELECT id, role, content, conversation_id, created_at, meta, "
                "rowid FROM messages WHERE conversation_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?"
            )
            query_params: tuple[object, ...] = (p.conversation_id, p.limit + 1)
        else:
            created_at, rowid = decode_message_page_cursor(p.cursor)
            query = (
                "SELECT id, role, content, conversation_id, created_at, meta, "
                "rowid FROM messages WHERE conversation_id = ? "
                "AND (created_at < ? OR (created_at = ? AND rowid < ?)) "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?"
            )
            query_params = (
                p.conversation_id,
                created_at,
                created_at,
                rowid,
                p.limit + 1,
            )
        rows = db.execute(query, query_params).fetchall()
        has_more = len(rows) > p.limit
        page_rows = rows[: p.limit]
        next_cursor = (
            encode_message_page_cursor(str(page_rows[-1][4]), int(page_rows[-1][6]))
            if has_more
            else None
        )
        return {
            "messages": [_message_entry(row) for row in page_rows],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    return handle


def make_count_memory_entries_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for count_memory_entries operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Count messages, records, and assertions."""
        validate_params(params, CountMemoryEntriesParams)
        return _table_counts(db, MEMORY_COUNT_QUERIES)

    return handle


# Tainted records and invalidated (forgotten) assertions must never
# surface through the MCP read surface, matching the FTS/vector filters.
RECENT_MEMORY_SQL = """
    SELECT 'message' AS table_type, id, role, content, conversation_id,
           created_at, meta
    FROM messages
    UNION ALL
    SELECT 'record' AS table_type, id, NULL AS role, summary AS content,
           conversation_id, created_at, NULL AS meta
    FROM records
    WHERE tainted = 0
    UNION ALL
    SELECT 'assertion' AS table_type, id, NULL AS role,
           assertion_text AS content, NULL AS conversation_id,
           created_at, NULL AS meta
    FROM assertions
    WHERE invalid_from IS NULL
    ORDER BY created_at DESC
"""


def make_mcp_memory_recent_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for mcp_memory_recent operation.

    Returns newest-first entries across messages/records/assertions,
    bounded by MAX_RECENT_WINDOW (50).
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return recent memory entries, newest-first."""
        p = validate_params(params, LimitOffsetParams)
        limit = min(p.limit, 50)  # MAX_RECENT_WINDOW
        rows = db.execute(
            RECENT_MEMORY_SQL + " LIMIT ? OFFSET ?",
            (limit, p.offset),
        ).fetchall()
        entries = []
        for row in rows:
            entries.append(
                {
                    "id": row[1],
                    "table": row[0],
                    "role": row[2],
                    "content": parse_content(row[3]),
                    "conversation_id": row[4],
                    "created_at": row[5],
                    "meta": row[6],
                }
            )
        return {"entries": entries}

    return handle


def make_mcp_memory_get_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for mcp_memory_get operation.

    Fetches a single entry by (entry_id, table).
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return one memory entry by id."""
        from ..params import GetMemoryEntryParams

        p = validate_params(params, GetMemoryEntryParams)
        table = p.table
        entry_id = p.entry_id

        # Validate table name
        allowed_tables = {"messages", "records", "assertions"}
        if table not in allowed_tables:
            return {"entry": None, "note": f"Invalid table: {table}"}

        if table == "messages":
            row = db.execute(
                "SELECT id, role, content, conversation_id, created_at, meta "
                "FROM messages WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                return {"entry": None}
            return {"entry": _message_entry(row)}

        if table == "records":
            row = db.execute(
                "SELECT id, summary, key_facts, action_items, conversation_id, "
                "created_at, tainted, source_message_ids, memory_kinds "
                "FROM records WHERE id = ? AND tainted = 0",
                (entry_id,),
            ).fetchone()
            if row is None:
                return {"entry": None}
            return {"entry": _record_entry(row)}

        # assertions
        row = db.execute(
            "SELECT id, subject_entity_id, relation, object_entity_id, "
            "object_value, assertion_text, trust_level, assertion_scope, "
            "valid_from, invalid_from, source_record_ids, created_at "
            "FROM assertions WHERE id = ? AND invalid_from IS NULL",
            (entry_id,),
        ).fetchone()
        if row is None:
            return {"entry": None}
        return {
            "entry": {
                "id": row[0],
                "subject_entity_id": row[1],
                "relation": row[2],
                "object_entity_id": row[3],
                "object_value": row[4],
                "assertion_text": row[5],
                "trust_level": row[6],
                "scope": row[7],
                "valid_from": row[8],
                "invalid_from": row[9],
                "source_record_ids": row[10],
                "created_at": row[11],
            }
        }

    return handle


def make_mcp_memory_stats_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for mcp_memory_stats operation.

    Returns totals, per-type counts, and date range without content.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return memory statistics: counts and date range."""
        validate_params(params, CountMemoryEntriesParams)
        counts = _table_counts(db, MEMORY_COUNT_QUERIES)
        total = sum(counts.values())

        # Get date range across all memory tables
        min_row = db.execute(
            "SELECT min(created_at) FROM messages "
            "UNION ALL SELECT min(created_at) FROM records "
            "UNION ALL SELECT min(created_at) FROM assertions"
        ).fetchall()
        min_ts = min((r[0] for r in min_row if r[0] is not None), default=None)

        max_row = db.execute(
            "SELECT max(created_at) FROM messages "
            "UNION ALL SELECT max(created_at) FROM records "
            "UNION ALL SELECT max(created_at) FROM assertions"
        ).fetchall()
        max_ts = max((r[0] for r in max_row if r[0] is not None), default=None)

        result: dict[str, Any] = {
            "total": total,
            "by_type": counts,
        }
        if min_ts is not None:
            result["earliest"] = min_ts
        if max_ts is not None:
            result["latest"] = max_ts
        return result

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
