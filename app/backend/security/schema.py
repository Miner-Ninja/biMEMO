"""Database schema definitions for SQLCipher tables.

Defines the SQL statements for creating all core tables: metadata,
conversations, messages, records (with tainted flag), and
summaries. All tables use foreign keys with cascade delete for
secure data removal. Schema is applied via DatabaseManager after
successful unlock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

# --- Schema version ---
SCHEMA_VERSION = 13

# --- Table creation SQL ---
CREATE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value BLOB NOT NULL
)
"""

CREATE_CONVERSATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    processing_status TEXT NOT NULL DEFAULT 'idle',
    processing_stage TEXT NOT NULL DEFAULT '',
    processing_started_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    channel TEXT,
    meta TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)
"""

CREATE_NOTES_TABLE = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    key_facts TEXT NOT NULL DEFAULT '[]',
    action_items TEXT NOT NULL DEFAULT '[]',
    memory_kinds TEXT NOT NULL DEFAULT '[]',
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    tainted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)
"""

CREATE_SUMMARIES_TABLE = """
CREATE TABLE IF NOT EXISTS summaries (
    id TEXT PRIMARY KEY,
    main_point TEXT NOT NULL,
    based_on TEXT NOT NULL DEFAULT '[]',
    trust_level TEXT NOT NULL DEFAULT 'medium',
    summary_scope TEXT NOT NULL DEFAULT 'long_term',
    trust_level_score INTEGER NOT NULL DEFAULT 2,
    source_record_ids TEXT NOT NULL DEFAULT '[]',
    record_relations TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
)
"""

CREATE_USER_AUTH_TABLE = """
CREATE TABLE IF NOT EXISTS user_auth (
    id INTEGER PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_FILE_CONTEXTS_TABLE = """
CREATE TABLE IF NOT EXISTS file_contexts (
    file_id TEXT PRIMARY KEY,
    file_kind TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    processing_mode TEXT NOT NULL,
    processing_reason TEXT NOT NULL DEFAULT '',
    summary_overview TEXT NOT NULL DEFAULT '',
    summary_structure TEXT NOT NULL DEFAULT '[]',
    summary_key_points TEXT NOT NULL DEFAULT '[]',
    summary_warnings TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
)
"""

CREATE_FILE_CONTEXT_CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS file_context_chunks (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    estimated_tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
)
"""

CREATE_FILE_CONTEXT_USAGE_TABLE = """
CREATE TABLE IF NOT EXISTS file_context_usage (
    file_id TEXT PRIMARY KEY,
    processing_mode TEXT NOT NULL,
    mode_reason TEXT NOT NULL DEFAULT '',
    user_query TEXT NOT NULL DEFAULT '',
    selected_chunk_ids TEXT NOT NULL DEFAULT '[]',
    selected_chunk_indexes TEXT NOT NULL DEFAULT '[]',
    used_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
)
"""

CREATE_CHAT_BRIDGE_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS chat_bridge_links (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    external_space_id TEXT NOT NULL,
    external_channel_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    sync_direction TEXT NOT NULL DEFAULT 'bidirectional',
    status TEXT NOT NULL DEFAULT 'active',
    delivery_mode TEXT NOT NULL DEFAULT 'bot_only',
    persona_mode TEXT NOT NULL DEFAULT 'neutral',
    persona_user_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    UNIQUE(conversation_id, provider),
    UNIQUE(provider, external_space_id, external_channel_id)
)
"""

CREATE_CHAT_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS chat_settings (
    conversation_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    PRIMARY KEY (conversation_id, key),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
)
"""

CREATE_SKILLS_TABLE = """
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    trigger_text TEXT NOT NULL DEFAULT '',
    procedure TEXT NOT NULL DEFAULT '[]',
    errors TEXT NOT NULL DEFAULT '[]',
    verification TEXT NOT NULL DEFAULT '',
    tool_sequence TEXT NOT NULL DEFAULT '',
    use_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_SKILL_USAGE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS skill_usage_log (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
)
"""

CREATE_SKILL_USAGE_LOG_SKILL_IDX = """
CREATE INDEX IF NOT EXISTS idx_skill_usage_log_skill_id
ON skill_usage_log(skill_id)
"""

CREATE_SKILLS_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts
USING fts5(name, title, trigger_text, verification, content='skills', content_rowid='rowid')
"""

CREATE_SKILLS_FTS_INSERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS skills_fts_insert AFTER INSERT ON skills BEGIN
    INSERT INTO skills_fts(rowid, name, title, trigger_text, verification)
    VALUES (new.rowid, new.name, new.title, new.trigger_text, new.verification);
END
"""

CREATE_SKILLS_FTS_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS skills_fts_update AFTER UPDATE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, title, trigger_text, verification)
    VALUES ('delete', old.rowid, old.name, old.title, old.trigger_text, old.verification);
    INSERT INTO skills_fts(rowid, name, title, trigger_text, verification)
    VALUES (new.rowid, new.name, new.title, new.trigger_text, new.verification);
END
"""

CREATE_SKILLS_FTS_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS skills_fts_delete AFTER DELETE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, title, trigger_text, verification)
    VALUES ('delete', old.rowid, old.name, old.title, old.trigger_text, old.verification);
END
"""

CREATE_AVATARS_TABLE = """
CREATE TABLE IF NOT EXISTS avatars (
    type TEXT PRIMARY KEY CHECK (type IN ('user', 'ai')),
    data BLOB NOT NULL,
    mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_SCHEDULED_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    task_type TEXT NOT NULL CHECK (task_type IN ('reminder', 'agent_task')),
    prompt TEXT NOT NULL,
    schedule_kind TEXT NOT NULL CHECK (schedule_kind IN ('once', 'interval', 'cron')),
    schedule_expr TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'completed')),
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    repeat_limit INTEGER,
    repeat_count INTEGER NOT NULL DEFAULT 0,
    allowed_tools TEXT NOT NULL DEFAULT '[]',
    conversation_id TEXT,
    deliver TEXT NOT NULL DEFAULT 'origin',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_SCHEDULED_TASKS_DUE_IDX = """
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
ON scheduled_tasks(next_run_at, status)
"""

CREATE_SCHEDULED_TASK_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('ok', 'error', 'timeout', 'silent', 'skipped')),
    result_text TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
)
"""

CREATE_SCHEDULED_TASK_RUNS_TASK_IDX = """
CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task
ON scheduled_task_runs(task_id, started_at DESC)
"""

CREATE_CHAT_BRIDGE_MESSAGE_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS chat_bridge_message_links (
    id TEXT PRIMARY KEY,
    bridge_link_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    external_message_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (bridge_link_id) REFERENCES chat_bridge_links(id) ON DELETE CASCADE,
    UNIQUE(bridge_link_id, external_message_id),
    UNIQUE(bridge_link_id, message_id, direction)
)
"""

# --- Index creation SQL ---
CREATE_MESSAGES_CONVERSATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
ON messages(conversation_id)
"""

CREATE_MESSAGES_CREATED_AT_IDX = """
CREATE INDEX IF NOT EXISTS idx_messages_created_at
ON messages(created_at)
"""

CREATE_NOTES_CONVERSATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_records_conversation_id
ON records(conversation_id)
"""

CREATE_NOTES_SESSION_IDX = """
CREATE INDEX IF NOT EXISTS idx_records_session_id
ON records(session_id)
"""

CREATE_NOTES_TAINTED_IDX = """
CREATE INDEX IF NOT EXISTS idx_records_tainted
ON records(tainted)
"""

CREATE_FILE_CONTEXT_CHUNKS_FILE_IDX = """
CREATE INDEX IF NOT EXISTS idx_file_context_chunks_file_id
ON file_context_chunks(file_id)
"""

CREATE_FILE_CONTEXT_CHUNKS_ORDER_IDX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_file_context_chunks_file_order
ON file_context_chunks(file_id, chunk_index)
"""

CREATE_CHAT_BRIDGE_LINKS_CONVERSATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_chat_bridge_links_conversation_id
ON chat_bridge_links(conversation_id)
"""

CREATE_CHAT_BRIDGE_LINKS_PROVIDER_IDX = """
CREATE INDEX IF NOT EXISTS idx_chat_bridge_links_provider
ON chat_bridge_links(provider)
"""

CREATE_CHAT_BRIDGE_MESSAGE_LINKS_BRIDGE_IDX = """
CREATE INDEX IF NOT EXISTS idx_chat_bridge_message_links_bridge_link_id
ON chat_bridge_message_links(bridge_link_id)
"""

# --- Ordered list for schema initialization ---
SCHEMA_STATEMENTS = [
    CREATE_SCHEMA_VERSION_TABLE,
    CREATE_METADATA_TABLE,
    CREATE_CONVERSATIONS_TABLE,
    CREATE_MESSAGES_TABLE,
    CREATE_NOTES_TABLE,
    CREATE_SUMMARIES_TABLE,
    CREATE_MESSAGES_CONVERSATION_IDX,
    CREATE_MESSAGES_CREATED_AT_IDX,
    CREATE_NOTES_CONVERSATION_IDX,
    CREATE_NOTES_SESSION_IDX,
    CREATE_NOTES_TAINTED_IDX,
    CREATE_USER_AUTH_TABLE,
    CREATE_FILES_TABLE,
    CREATE_FILE_CONTEXTS_TABLE,
    CREATE_FILE_CONTEXT_CHUNKS_TABLE,
    CREATE_FILE_CONTEXT_USAGE_TABLE,
    CREATE_CHAT_BRIDGE_LINKS_TABLE,
    CREATE_CHAT_BRIDGE_MESSAGE_LINKS_TABLE,
    CREATE_FILE_CONTEXT_CHUNKS_FILE_IDX,
    CREATE_FILE_CONTEXT_CHUNKS_ORDER_IDX,
    CREATE_CHAT_BRIDGE_LINKS_CONVERSATION_IDX,
    CREATE_CHAT_BRIDGE_LINKS_PROVIDER_IDX,
    CREATE_CHAT_BRIDGE_MESSAGE_LINKS_BRIDGE_IDX,
    CREATE_CHAT_SETTINGS_TABLE,
    CREATE_SKILLS_TABLE,
    CREATE_SKILL_USAGE_LOG_TABLE,
    CREATE_SKILL_USAGE_LOG_SKILL_IDX,
    CREATE_SKILLS_FTS_TABLE,
    CREATE_SKILLS_FTS_INSERT_TRIGGER,
    CREATE_SKILLS_FTS_UPDATE_TRIGGER,
    CREATE_SKILLS_FTS_DELETE_TRIGGER,
    CREATE_AVATARS_TABLE,
    CREATE_SCHEDULED_TASKS_TABLE,
    CREATE_SCHEDULED_TASKS_DUE_IDX,
    CREATE_SCHEDULED_TASK_RUNS_TABLE,
    CREATE_SCHEDULED_TASK_RUNS_TASK_IDX,
]


def _schema_statements() -> list[str]:
    """Return the ordered core schema statements."""
    return SCHEMA_STATEMENTS


def _apply_schema_statements(
    db: DatabaseManager,
    statements: list[str],
) -> None:
    """Execute one ordered list of schema statements."""
    for statement in statements:
        db.execute(statement)


def _table_columns(
    db: DatabaseManager,
    table_name: str,
) -> set[str]:
    """Return the current column names for one table."""
    return {row[1] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_schema_version(
    db: DatabaseManager,
) -> None:
    """Insert or update the stored schema version row."""
    cursor = db.execute("SELECT version FROM schema_version LIMIT 1")
    version_row = cursor.fetchone()
    if version_row is None:
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif version_row[0] < SCHEMA_VERSION:
        db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


def _ensure_messages_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns required by the current messages schema."""
    columns = _table_columns(db, "messages")
    if "meta" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN meta TEXT")


def _ensure_conversations_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns required by the current conversations schema."""
    columns = _table_columns(db, "conversations")
    if "processing_status" not in columns:
        db.execute(
            "ALTER TABLE conversations "
            "ADD COLUMN processing_status TEXT NOT NULL DEFAULT 'idle'"
        )
    if "processing_stage" not in columns:
        db.execute(
            "ALTER TABLE conversations "
            "ADD COLUMN processing_stage TEXT NOT NULL DEFAULT ''"
        )
    if "processing_started_at" not in columns:
        db.execute("ALTER TABLE conversations ADD COLUMN processing_started_at TEXT")
    if "conversation_summary" not in columns:
        db.execute(
            "ALTER TABLE conversations "
            "ADD COLUMN conversation_summary TEXT NOT NULL DEFAULT ''"
        )


def _ensure_records_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns required by the current records schema."""
    columns = _table_columns(db, "records")
    if "source_message_ids" not in columns:
        db.execute(
            "ALTER TABLE records "
            "ADD COLUMN source_message_ids TEXT NOT NULL DEFAULT '[]'"
        )
    if "memory_kinds" not in columns:
        db.execute(
            "ALTER TABLE records ADD COLUMN memory_kinds TEXT NOT NULL DEFAULT '[]'"
        )


def _ensure_summaries_schema(
    db: DatabaseManager,
) -> None:
    """Validate or migrate the summaries table to the current schema."""
    summary_columns = _table_columns(db, "summaries")
    missing_summary_columns = {
        "main_point",
        "based_on",
        "trust_level",
        "summary_scope",
        "trust_level_score",
        "record_relations",
    } - summary_columns
    if missing_summary_columns == {"summary_scope"}:
        db.execute(
            "ALTER TABLE summaries "
            "ADD COLUMN summary_scope TEXT NOT NULL DEFAULT 'long_term'"
        )
    elif missing_summary_columns:
        raise RuntimeError(
            "Summaries table uses an outdated schema. "
            "Delete and recreate the database with the current schema."
        )


def _ensure_file_context_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns required by the current file-context schema."""
    columns = _table_columns(db, "file_contexts")
    if "processing_reason" not in columns:
        db.execute(
            "ALTER TABLE file_contexts "
            "ADD COLUMN processing_reason TEXT NOT NULL DEFAULT ''"
        )


def _ensure_chat_bridge_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns required by the current chat-bridge schema."""
    columns = _table_columns(db, "chat_bridge_links")
    if "delivery_mode" not in columns:
        db.execute(
            "ALTER TABLE chat_bridge_links "
            "ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'bot_only'"
        )
    if "persona_mode" not in columns:
        db.execute(
            "ALTER TABLE chat_bridge_links "
            "ADD COLUMN persona_mode TEXT NOT NULL DEFAULT 'neutral'"
        )
    if "persona_user_id" not in columns:
        db.execute(
            "ALTER TABLE chat_bridge_links "
            "ADD COLUMN persona_user_id TEXT NOT NULL DEFAULT ''"
        )


def _parse_content_sections(content: str) -> dict[str, str]:
    """Parse markdown content into structured sections by ## headers."""
    sections: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in content.split("\n"):
        stripped = line.strip().lower()
        if stripped.startswith("## "):
            if current_key and current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            header = stripped[3:].strip()
            if "use" in header or "trigger" in header or "когда" in header:
                current_key = "trigger"
            elif "procedure" in header or "процедур" in header or "шаг" in header:
                current_key = "procedure"
            elif "pitfall" in header or "error" in header or "ошибк" in header:
                current_key = "errors"
            elif "verif" in header or "провер" in header:
                current_key = "verification"
            else:
                current_key = "trigger"
            current_lines = []
        elif current_key:
            current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _ensure_skills_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns and migrate content to structured fields."""
    columns = _table_columns(db, "skills")
    if "enabled" not in columns:
        db.execute("ALTER TABLE skills ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if "trigger_text" not in columns:
        db.execute(
            "ALTER TABLE skills ADD COLUMN trigger_text TEXT NOT NULL DEFAULT ''"
        )
        db.execute("ALTER TABLE skills ADD COLUMN procedure TEXT NOT NULL DEFAULT '[]'")
        db.execute("ALTER TABLE skills ADD COLUMN errors TEXT NOT NULL DEFAULT '[]'")
        db.execute(
            "ALTER TABLE skills ADD COLUMN verification TEXT NOT NULL DEFAULT ''"
        )
        # Migrate existing content to structured columns
        if "content" in columns:
            rows = db.execute("SELECT id, content FROM skills").fetchall()
            for row in rows:
                skill_id, content = row[0], row[1] or ""
                sections = _parse_content_sections(content)
                db.execute(
                    "UPDATE skills SET trigger_text = ?, verification = ? WHERE id = ?",
                    (
                        sections.get("trigger", content),
                        sections.get("verification", ""),
                        skill_id,
                    ),
                )


def _ensure_scheduled_tasks_schema(
    db: DatabaseManager,
) -> None:
    """Add missing columns required by the current scheduled_tasks schema."""
    columns = _table_columns(db, "scheduled_tasks")
    if "timezone" not in columns:
        db.execute(
            "ALTER TABLE scheduled_tasks "
            "ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"
        )


def _migrate_reminders_to_scheduled_tasks(
    db: DatabaseManager,
) -> None:
    """Migrate active reminders to scheduled_tasks and drop the reminders table."""
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "reminders" not in tables:
        return
    rows = db.execute(
        "SELECT id, title, due_at, description, status, created_at "
        "FROM reminders WHERE status = 'active'"
    ).fetchall()
    for row in rows:
        reminder_id, title, due_at, description, _status, created_at = row
        prompt = f"{title}: {description}" if description else title
        db.execute(
            "INSERT OR IGNORE INTO scheduled_tasks "
            "(id, name, task_type, prompt, schedule_kind, schedule_expr, "
            "next_run_at, status, created_at) "
            "VALUES (?, ?, 'reminder', ?, 'once', ?, ?, 'active', ?)",
            (reminder_id, title, prompt, due_at, due_at, created_at),
        )
    db.execute("DROP TABLE IF EXISTS reminders")


def initialize_schema(
    db: DatabaseManager,
) -> None:
    """Create all database tables and indexes if they do not exist.

    Args:
        db: An open DatabaseManager instance.
    """
    _apply_schema_statements(db, _schema_statements())
    _ensure_schema_version(db)
    _ensure_messages_schema(db)
    _ensure_conversations_schema(db)
    _ensure_records_schema(db)
    _ensure_summaries_schema(db)
    _ensure_file_context_schema(db)
    _ensure_chat_bridge_schema(db)
    _ensure_skills_schema(db)
    _migrate_reminders_to_scheduled_tasks(db)
    _ensure_scheduled_tasks_schema(db)
    db.commit()
