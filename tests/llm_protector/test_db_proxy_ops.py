"""Tests for DB Proxy process operations.

Covers named operations, parameter validation, allowlist
enforcement, and signing key derivation. Uses an in-memory SQLite
database as a stand-in for the encrypted database.
"""

import sqlite3

import pytest
from pydantic import ValidationError

from backend.ipc.models import DBOperation
from backend.processes.db_proxy import (
    CHANNEL_A,
    CHANNEL_B,
    SOCKET_A_OPERATIONS,
    SOCKET_B_ONLY_OPERATIONS,
    SOCKET_B_OPERATIONS,
    SOCKET_C_OPERATIONS,
    FulltextSearchParams,
    SaveMessageParams,
    StoreCredentialParams,
    WriteAuditParams,
    build_allowlist,
    build_registry,
    make_bump_queued_record_attempts_handler,
    make_create_conversation_handler,
    make_create_integration_handler,
    make_delete_conversation_handler,
    make_delete_integration_handler,
    make_delete_queued_records_handler,
    make_fetch_unembedded_records_handler,
    make_get_credential_handler,
    make_get_signing_material_handler,
    make_get_stats_handler,
    make_list_conversations_handler,
    make_list_integrations_handler,
    make_list_queued_records_handler,
    make_load_context_handler,
    make_load_recent_messages_handler,
    make_save_message_handler,
    make_save_queued_record_handler,
    make_save_record_handler,
    make_store_credential_handler,
    make_update_conversation_processing_state_handler,
    make_update_conversation_title_handler,
    make_verify_chain_handler,
    make_write_audit_handler,
)


EXPECTED_SOCKET_A_OPERATION_COUNT = 104
EXPECTED_SOCKET_B_ONLY_OPERATION_COUNT = 68
EXPECTED_SOCKET_B_OPERATION_COUNT = 101
EXPECTED_SOCKET_C_OPERATION_COUNT = 14

SCHEDULED_TASK_SOCKET_A_OPERATIONS = {
    "create_scheduled_task",
    "list_scheduled_tasks",
    "update_scheduled_task",
    "delete_scheduled_task",
}


class FakeDB:
    """Minimal ``DatabaseManager`` stand-in using in-memory SQLite."""

    def __init__(self) -> None:
        """Initialize the in-memory database and create required tables."""
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create all tables used by the tested DB proxy handlers."""
        statements = [
            """CREATE TABLE metadata (key TEXT PRIMARY KEY, value BLOB NOT NULL)""",
            """CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                processing_status TEXT NOT NULL DEFAULT 'idle',
                processing_stage TEXT NOT NULL DEFAULT '',
                processing_started_at TEXT,
                conversation_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
            )""",
            """CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                channel TEXT,
                meta TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE records (
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
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE record_queue (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                messages TEXT NOT NULL DEFAULT '[]',
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                injection_flagged INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
            )""",
            """CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                UNIQUE (entity_type, canonical_name)
            )""",
            """CREATE TABLE assertions (
                id TEXT PRIMARY KEY,
                subject_entity_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                object_entity_id TEXT,
                object_value TEXT,
                assertion_text TEXT NOT NULL,
                trust_level TEXT NOT NULL DEFAULT 'medium',
                assertion_scope TEXT NOT NULL DEFAULT 'long_term',
                valid_from TEXT,
                invalid_from TEXT,
                invalidated_by_assertion_id TEXT,
                source_record_ids TEXT NOT NULL DEFAULT '[]',
                migrated_from_summary_id TEXT UNIQUE,
                created_at TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                CHECK (object_entity_id IS NULL OR object_value IS NULL),
                FOREIGN KEY (subject_entity_id) REFERENCES entities(id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (object_entity_id) REFERENCES entities(id)
                    ON DELETE RESTRICT
            )""",
            """CREATE TABLE reminders (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                due_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                tool_name TEXT,
                parameters TEXT,
                result TEXT,
                zone TEXT,
                duration_ms REAL,
                previous_hash TEXT NOT NULL,
                entry_hash TEXT NOT NULL
            )""",
            """CREATE TABLE integrations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                service_type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
            )""",
            """CREATE TABLE credentials (
                id TEXT PRIMARY KEY,
                integration_id TEXT NOT NULL,
                credential_type TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                FOREIGN KEY (integration_id)
                    REFERENCES integrations(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE record_embeddings (
                record_id TEXT PRIMARY KEY
            )""",
            """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
               USING fts5(content, content_rowid='rowid')""",
            """CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
               USING fts5(summary, key_facts, content_rowid='rowid')""",
        ]
        for statement in statements:
            self.connection.execute(statement)
        self.connection.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement."""
        return self.connection.execute(sql, params)

    def executemany(
        self,
        sql: str,
        params_seq: list[tuple],
    ) -> sqlite3.Cursor:
        """Execute a SQL statement for multiple parameter sets."""
        return self.connection.executemany(sql, params_seq)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()

    @property
    def is_open(self) -> bool:
        """Check whether the database connection is active."""
        return self.connection is not None


@pytest.fixture
def db() -> FakeDB:
    """Create a fresh in-memory test database."""
    return FakeDB()


class TestParameterValidation:
    """Verify Pydantic parameter model validation."""

    def test_save_message_params_valid(self) -> None:
        """Accepts valid ``save_message`` parameters."""
        params = SaveMessageParams(
            conversation_id="c1",
            role="user",
            content="hello",
        )
        assert params.conversation_id == "c1"
        assert params.channel is None

    def test_save_message_params_extra_rejected(self) -> None:
        """Rejects unexpected fields in ``save_message`` parameters."""
        with pytest.raises(ValidationError, match="extra"):
            SaveMessageParams(
                conversation_id="c1",
                role="user",
                content="hi",
                extra="bad",
            )

    def test_write_audit_params_defaults(self) -> None:
        """Accepts ``write_audit`` with only required fields."""
        params = WriteAuditParams(event_type="tool_call")
        assert params.tool_name is None
        assert params.duration_ms is None

    def test_fulltext_search_params_defaults(self) -> None:
        """Uses the expected defaults for full-text search."""
        params = FulltextSearchParams(query="test")
        assert params.table == "messages"
        assert params.limit == 20

    def test_store_credential_params_required(self) -> None:
        """Requires all mandatory fields for ``store_credential``."""
        with pytest.raises(ValidationError, match="Field required"):
            StoreCredentialParams(integration_id="i1")


class TestMessageOperations:
    """Verify message-related DB proxy handlers."""

    @pytest.mark.asyncio
    async def test_save_message(self, db: FakeDB) -> None:
        """Saves a message and returns a UUID-like identifier."""
        handler = make_save_message_handler(db)
        result = await handler(
            {
                "conversation_id": "c1",
                "role": "user",
                "content": "hello",
            }
        )
        assert "message_id" in result
        assert len(result["message_id"]) == 36

    @pytest.mark.asyncio
    async def test_load_recent_messages(self, db: FakeDB) -> None:
        """Loads saved messages for a conversation in chronological order."""
        save_message = make_save_message_handler(db)
        await save_message({"conversation_id": "c1", "role": "user", "content": "msg1"})
        await save_message(
            {"conversation_id": "c1", "role": "assistant", "content": "msg2"}
        )

        load_messages = make_load_recent_messages_handler(db)
        result = await load_messages({"conversation_id": "c1"})
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["content"] == "msg2"

    @pytest.mark.asyncio
    async def test_load_recent_messages_empty(self, db: FakeDB) -> None:
        """Returns an empty list for an unknown conversation."""
        handler = make_load_recent_messages_handler(db)
        result = await handler({"conversation_id": "nonexistent"})
        assert result["messages"] == []


class TestContextOperation:
    """Verify baseline context loading (graph era)."""

    @pytest.mark.asyncio
    async def test_load_context_baseline(self, db: FakeDB) -> None:
        """Returns records, messages, and the S-zone summary."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary) "
            "VALUES (?, ?, ?, ?)",
            ("o1", "c1", "s1", "obs"),
        )
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("m1", "c1", "user", "hello"),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler({"session_id": "s1", "conversation_id": "c1"})
        assert len(result["records"]) == 1
        assert len(result["messages"]) == 1
        assert "conversation_summary" in result
        assert "summaries" not in result



class TestAuditOperations:
    """Verify audit log writing and integrity checks."""

    @pytest.mark.asyncio
    async def test_write_and_verify_audit(self, db: FakeDB) -> None:
        """Writes audit entries and verifies the chain."""
        writer = make_write_audit_handler(db)
        verifier = make_verify_chain_handler(db)

        await writer({"event_type": "tool_call", "tool_name": "file_read"})
        await writer({"event_type": "tool_call", "tool_name": "web_search"})

        result = await verifier(None)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_verify_empty_chain(self, db: FakeDB) -> None:
        """Treats an empty audit chain as valid."""
        verifier = make_verify_chain_handler(db)
        result = await verifier(None)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_tampered_chain_detected(self, db: FakeDB) -> None:
        """Detects tampering in an existing audit chain."""
        writer = make_write_audit_handler(db)
        await writer({"event_type": "tool_call"})
        await writer({"event_type": "login"})

        db.execute("UPDATE audit_log SET event_type = 'hacked' WHERE id = 1")
        db.commit()

        verifier = make_verify_chain_handler(db)
        result = await verifier(None)
        assert result["valid"] is False


class TestRecordOperations:
    """Verify record persistence and tainting."""

    @pytest.mark.asyncio
    async def test_save_record(self, db: FakeDB) -> None:
        """Saves an record for an existing conversation."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.commit()

        handler = make_save_record_handler(db)
        result = await handler(
            {
                "conversation_id": "c1",
                "session_id": "s1",
                "summary": "test summary",
                "key_facts": ["fact1"],
                "memory_kinds": ["preference"],
                "source_message_ids": ["m1", "m2"],
            }
        )
        assert "record_id" in result
        row = db.execute(
            "SELECT memory_kinds, source_message_ids FROM records WHERE id = ?",
            (result["record_id"],),
        ).fetchone()
        assert row is not None
        assert row[0] == '["preference"]'
        assert row[1] == '["m1", "m2"]'

    @pytest.mark.asyncio
    async def test_save_record_skips_deleted_conversation(self, db: FakeDB) -> None:
        """Missing parent conversation turns memory write into a skipped side effect."""
        handler = make_save_record_handler(db)

        result = await handler(
            {
                "conversation_id": "deleted-conv",
                "session_id": "s1",
                "summary": "test summary",
            }
        )

        assert result == {
            "record_id": "",
            "skipped": True,
            "reason": "conversation_deleted",
        }
        row = db.execute(
            "SELECT id FROM records WHERE conversation_id = ?",
            ("deleted-conv",),
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_save_record_tainted_at_insert(self, db: FakeDB) -> None:
        """Stores a tainted record with the flag set at insert time."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.commit()

        handler = make_save_record_handler(db)
        result = await handler(
            {
                "conversation_id": "c1",
                "session_id": "s1",
                "summary": "flagged content",
                "tainted": True,
            }
        )

        row = db.execute(
            "SELECT tainted FROM records WHERE id = ?",
            (result["record_id"],),
        ).fetchone()
        assert row[0] == 1


class TestUiSettingsOperations:
    """R1 (ui-settings-db) — settings blob roundtrip and size cap."""

    @pytest.mark.asyncio
    async def test_roundtrip_and_empty_default(self, db: FakeDB) -> None:
        """S1.1 + S1.2 — stored blob returns as-is; empty reads as {}."""
        from backend.processes.db_proxy.handlers.admin import (
            make_get_ui_settings_handler,
            make_store_ui_settings_handler,
        )

        get_handler = make_get_ui_settings_handler(db)
        empty = await get_handler({})
        assert empty["settings"] == {}

        store_handler = make_store_ui_settings_handler(db)
        payload = {
            "theme": "dark",
            "voice_mode": True,
            "command_history": ["ls", "htop"],
        }
        await store_handler({"settings": payload})
        stored = await get_handler({})
        assert stored["settings"] == payload

    @pytest.mark.asyncio
    async def test_oversized_blob_rejected(self, db: FakeDB) -> None:
        """S1.3 — an over-cap blob fails and the old value survives."""
        from backend.processes.db_proxy.handlers.admin import (
            make_get_ui_settings_handler,
            make_store_ui_settings_handler,
        )

        store_handler = make_store_ui_settings_handler(db)
        await store_handler({"settings": {"theme": "light"}})
        with pytest.raises(ValueError, match="exceeds"):
            await store_handler(
                {"settings": {"command_history": ["x" * 200] * 200}}
            )
        get_handler = make_get_ui_settings_handler(db)
        stored = await get_handler({})
        assert stored["settings"] == {"theme": "light"}


class TestListEntitiesOperation:
    """R1 (memory-page) — paged entity listing with active-fact counts."""

    @pytest.mark.asyncio
    async def test_counts_only_active_assertions(self, db: FakeDB) -> None:
        """S1.1 — each entity carries its count of active assertions."""
        from backend.processes.db_proxy.handlers.graph import (
            make_list_entities_handler,
        )

        db.execute(
            "INSERT INTO entities (id, entity_type, canonical_name) "
            "VALUES ('e1', 'person', 'User'), ('e2', 'tool', 'Docker')"
        )
        db.execute(
            "INSERT INTO assertions "
            "(id, subject_entity_id, relation, object_value, assertion_text) "
            "VALUES "
            "('a1', 'e1', 'uses', 'x', 'User uses x'), "
            "('a2', 'e1', 'prefers', 'y', 'User prefers y'), "
            "('a3', 'e2', 'states', 'z', 'Docker states z')"
        )
        db.execute(
            "UPDATE assertions SET invalid_from = '2026-01-01' WHERE id = 'a2'"
        )
        handler = make_list_entities_handler(db)
        result = await handler({"limit": 10, "offset": 0})
        assert result["total"] == 2
        by_id = {e["id"]: e for e in result["entities"]}
        assert by_id["e1"]["active_assertions"] == 1
        assert by_id["e2"]["active_assertions"] == 1

    @pytest.mark.asyncio
    async def test_pagination(self, db: FakeDB) -> None:
        """S1.1 — limit/offset page through entities."""
        from backend.processes.db_proxy.handlers.graph import (
            make_list_entities_handler,
        )

        for i in range(5):
            db.execute(
                "INSERT INTO entities (id, entity_type, canonical_name, "
                "created_at) VALUES (?, 'topic', ?, ?)",
                (f"e{i}", f"name{i}", f"2026-01-0{i + 1}T00:00:00"),
            )
        handler = make_list_entities_handler(db)
        page = await handler({"limit": 2, "offset": 2})
        assert page["total"] == 5
        assert [e["id"] for e in page["entities"]] == ["e2", "e1"]


class TestGetEntitiesByIds:
    """memory-entity-page — batch neighbor resolution."""

    @pytest.mark.asyncio
    async def test_returns_requested_entities(self, db: FakeDB) -> None:
        """Only the requested ids are returned; unknown ids are ignored."""
        from backend.processes.db_proxy.handlers.graph import (
            make_get_entities_by_ids_handler,
        )

        db.execute(
            "INSERT INTO entities (id, entity_type, canonical_name) VALUES "
            "('e1', 'person', 'User'), ('e2', 'tool', 'Docker'), "
            "('e3', 'topic', 'Graphs')"
        )
        handler = make_get_entities_by_ids_handler(db)
        result = await handler({"entity_ids": ["e1", "e3", "missing"]})
        by_id = {e["id"]: e for e in result["entities"]}
        assert set(by_id) == {"e1", "e3"}
        assert by_id["e1"]["canonical_name"] == "User"

    @pytest.mark.asyncio
    async def test_empty_input(self, db: FakeDB) -> None:
        """An empty id list returns no entities and runs no query."""
        from backend.processes.db_proxy.handlers.graph import (
            make_get_entities_by_ids_handler,
        )

        handler = make_get_entities_by_ids_handler(db)
        result = await handler({"entity_ids": []})
        assert result == {"entities": []}


class TestPeekRecordQueue:
    """memory-diagnostics-gaps — read-only queue view (no pruning)."""

    @pytest.mark.asyncio
    async def test_returns_contents_without_pruning(self, db: FakeDB) -> None:
        """Rows are returned oldest-first and an old row is NOT deleted."""
        from backend.processes.db_proxy.handlers.memory import (
            make_peek_record_queue_handler,
        )

        db.execute(
            "INSERT INTO record_queue "
            "(id, conversation_id, session_id, source_message_ids, attempts, "
            "created_at) VALUES "
            "('q1', 'c1', 's1', '[\"m1\"]', 2, '2000-01-01T00:00:00'), "
            "('q2', 'c2', 's2', '[]', 0, '2030-01-01T00:00:00')"
        )
        handler = make_peek_record_queue_handler(db)
        result = await handler({"limit": 10})
        assert [item["queue_id"] for item in result["items"]] == ["q1", "q2"]
        assert result["items"][0]["attempts"] == 2
        assert result["items"][0]["source_message_ids"] == ["m1"]
        assert result["items"][0]["created_at"] == "2000-01-01T00:00:00"
        # The ancient row must survive — this is a read, not the pruning op.
        remaining = db.execute("SELECT COUNT(*) FROM record_queue").fetchone()[0]
        assert remaining == 2


class TestVectorSearch:
    """hybrid-recall — KNN vector search joins records and drops tainted."""

    @pytest.mark.asyncio
    async def test_joins_records_excludes_tainted_orders_by_distance(
        self, db: FakeDB, monkeypatch
    ) -> None:
        """S2.1 + S2.2 — nearest untainted records returned, tainted dropped."""
        import backend.memory.retrieval.vector_store as vector_store
        from backend.processes.db_proxy.handlers.memory import (
            make_vector_search_handler,
        )

        db.execute("INSERT INTO conversations (id) VALUES ('c1')")
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary, "
            "tainted) VALUES "
            "('r1', 'c1', 's1', 'first', 0), "
            "('r2', 'c1', 's1', 'second', 0), "
            "('r3', 'c1', 's1', 'tainted one', 1)"
        )

        # vec0 MATCH is unavailable in the plain FakeDB — stub the KNN so the
        # test exercises the handler's join/tainted/ordering logic.
        def fake_search_similar(_db, _embedding, _limit):
            return [("r3", 0.05), ("r1", 0.10), ("r2", 0.20)]

        monkeypatch.setattr(vector_store, "search_similar", fake_search_similar)

        handler = make_vector_search_handler(db)
        result = await handler({"query_embedding": [0.1, 0.2], "limit": 5})
        ids = [row["id"] for row in result["results"]]
        assert ids == ["r1", "r2"]  # r3 tainted excluded; nearest-first
        assert result["results"][0]["distance"] == 0.10
        assert result["results"][0]["summary"] == "first"

    @pytest.mark.asyncio
    async def test_empty_embedding_returns_nothing(self, db: FakeDB) -> None:
        """An empty query embedding short-circuits to no results."""
        from backend.processes.db_proxy.handlers.memory import (
            make_vector_search_handler,
        )

        handler = make_vector_search_handler(db)
        result = await handler({"query_embedding": [], "limit": 5})
        assert result == {"results": []}


class TestVectorCoverage:
    """memory-diagnostics-gaps — embedding coverage metric."""

    @pytest.mark.asyncio
    async def test_counts_embedded_vs_total(self, db: FakeDB) -> None:
        """Coverage counts non-tainted records with an embedding."""
        from backend.processes.db_proxy.handlers.memory import (
            make_get_vector_coverage_handler,
        )

        db.execute("INSERT INTO conversations (id) VALUES ('c1')")
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary, "
            "tainted) VALUES "
            "('r1', 'c1', 's1', 'a', 0), "
            "('r2', 'c1', 's1', 'b', 0), "
            "('r3', 'c1', 's1', 'c', 1)"
        )
        db.execute("INSERT INTO record_embeddings (record_id) VALUES ('r1')")
        handler = make_get_vector_coverage_handler(db)
        result = await handler({})
        assert result["total"] == 2  # tainted r3 excluded
        assert result["embedded"] == 1
        assert result["unembedded"] == 1

    @pytest.mark.asyncio
    async def test_missing_vector_table(self, db: FakeDB) -> None:
        """With no embeddings table, all records read as unembedded."""
        from backend.processes.db_proxy.handlers.memory import (
            make_get_vector_coverage_handler,
        )

        db.execute("DROP TABLE record_embeddings")
        db.execute("INSERT INTO conversations (id) VALUES ('c1')")
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary, "
            "tainted) VALUES ('r1', 'c1', 's1', 'a', 0)"
        )
        handler = make_get_vector_coverage_handler(db)
        result = await handler({})
        assert result == {"total": 1, "embedded": 0, "unembedded": 1}


class TestCreateBackup:
    """Verify consistent backups via VACUUM INTO."""

    @pytest.mark.asyncio
    async def test_backup_writes_every_database(self, tmp_path) -> None:
        """Each open database gets its own consistent backup file."""
        from backend.processes.db_proxy.handlers.admin import (
            make_create_backup_handler,
        )

        core = FakeDB()
        runtime = FakeDB()
        core.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        core.commit()

        handler = make_create_backup_handler(
            {"core": core, "runtime": runtime},
            str(tmp_path / "bimemo.db"),
        )
        result = await handler({})

        assert set(result["backup_paths"]) == {"core", "runtime"}
        core_backup = result["backup_paths"]["core"]
        copy = sqlite3.connect(core_backup)
        rows = copy.execute("SELECT id FROM conversations").fetchall()
        copy.close()
        assert rows == [("c1",)]


class TestRecordQueueOperations:
    """Verify durable dialog-recorder queue rows."""

    @pytest.mark.asyncio
    async def test_queue_roundtrip(self, db: FakeDB) -> None:
        """Persist, list, bump, and delete pending queue rows."""
        save_handler = make_save_queued_record_handler(db)
        saved = await save_handler(
            {
                "conversation_id": "c1",
                "session_id": "s1",
                "messages": ["User: hello", "Assistant: hi"],
                "source_message_ids": ["m1"],
            }
        )
        queue_id = saved["queue_id"]

        list_handler = make_list_queued_records_handler(db)
        listed = await list_handler({})
        assert len(listed["items"]) == 1
        item = listed["items"][0]
        assert item["queue_id"] == queue_id
        assert item["messages"] == ["User: hello", "Assistant: hi"]
        assert item["attempts"] == 0

        bump_handler = make_bump_queued_record_attempts_handler(db)
        await bump_handler({"queue_ids": [queue_id]})
        listed = await list_handler({})
        assert listed["items"][0]["attempts"] == 1

        delete_handler = make_delete_queued_records_handler(db)
        await delete_handler({"queue_ids": [queue_id]})
        listed = await list_handler({})
        assert listed["items"] == []



class TestConversationOperations:
    """Verify conversation lifecycle handlers."""

    @pytest.mark.asyncio
    async def test_create_and_list_conversations(self, db: FakeDB) -> None:
        """Creates a conversation and returns it from the list handler."""
        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "test chat"})
        assert "conversation_id" in result

        list_conversations = make_list_conversations_handler(db)
        result = await list_conversations({})
        assert len(result["conversations"]) == 1
        assert result["conversations"][0]["processing_status"] == "idle"

    @pytest.mark.asyncio
    async def test_create_conversation_honours_pinned_id(self, db: FakeDB) -> None:
        """A caller-supplied conversation_id is used and is idempotent."""
        create_conversation = make_create_conversation_handler(db)

        result = await create_conversation(
            {"conversation_id": "sched_task_1", "title": "[Scheduled] job"}
        )
        assert result["conversation_id"] == "sched_task_1"

        # Repeating the same pinned id must not fail on the primary key.
        again = await create_conversation(
            {"conversation_id": "sched_task_1", "title": "[Scheduled] job"}
        )
        assert again["conversation_id"] == "sched_task_1"

        list_conversations = make_list_conversations_handler(db)
        listed = await list_conversations({})
        assert sum(1 for c in listed["conversations"] if c["id"] == "sched_task_1") == 1

    @pytest.mark.asyncio
    async def test_save_message_updates_conversation_title_and_timestamp(
        self,
        db: FakeDB,
    ) -> None:
        """Promote title from first user message and refresh updated_at."""
        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": ""})
        conversation_id = result["conversation_id"]

        before_row = db.execute(
            "SELECT title, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        assert before_row[0] == ""

        save_message = make_save_message_handler(db)
        await save_message(
            {
                "conversation_id": conversation_id,
                "role": "user",
                "content": "  Server backed chat title   ",
            }
        )

        after_row = db.execute(
            "SELECT title, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        assert after_row[0] == "Server backed chat title"
        assert str(after_row[1]) >= str(before_row[1])

    @pytest.mark.asyncio
    async def test_update_conversation_title_updates_row(self, db: FakeDB) -> None:
        """Update one stored conversation title through the handler."""
        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "Original"})
        conversation_id = result["conversation_id"]

        update_conversation_title = make_update_conversation_title_handler(db)
        result = await update_conversation_title(
            {
                "conversation_id": conversation_id,
                "title": "Renamed from handler",
            }
        )

        assert result["conversation"]["id"] == conversation_id
        assert result["conversation"]["title"] == "Renamed from handler"

    @pytest.mark.asyncio
    async def test_update_conversation_processing_state_updates_row(
        self,
        db: FakeDB,
    ) -> None:
        """Update one stored conversation processing state through the handler."""
        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "Original"})
        conversation_id = result["conversation_id"]

        update_processing_state = make_update_conversation_processing_state_handler(db)
        result = await update_processing_state(
            {
                "conversation_id": conversation_id,
                "processing_status": "active",
                "processing_stage": "Processing...",
                "processing_started_at": "2026-03-25T11:00:00.000000",
            }
        )

        assert result["conversation"]["id"] == conversation_id
        assert result["conversation"]["processing_status"] == "active"
        assert result["conversation"]["processing_stage"] == "Processing..."
        assert (
            result["conversation"]["processing_started_at"]
            == "2026-03-25T11:00:00.000000"
        )

    @pytest.mark.asyncio
    async def test_delete_conversation_cascades(self, db: FakeDB) -> None:
        """Deletes a conversation and cascades to related messages."""
        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "to delete"})
        conversation_id = result["conversation_id"]

        save_message = make_save_message_handler(db)
        await save_message(
            {"conversation_id": conversation_id, "role": "user", "content": "hi"}
        )

        delete_conversation = make_delete_conversation_handler(db)
        await delete_conversation({"conversation_id": conversation_id})

        row = db.execute(
            "SELECT count(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_delete_conversation_skips_missing_embedding_table(
        self,
        db: FakeDB,
    ) -> None:
        """Deletes a conversation even when the embedding table is absent."""
        db.execute("DROP TABLE record_embeddings")
        db.commit()

        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "legacy db"})
        conversation_id = result["conversation_id"]

        save_message = make_save_message_handler(db)
        await save_message(
            {"conversation_id": conversation_id, "role": "user", "content": "hi"}
        )

        delete_conversation = make_delete_conversation_handler(db)
        await delete_conversation({"conversation_id": conversation_id})

        row = db.execute(
            "SELECT count(*) FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_delete_conversation_cleans_message_fts_rows(
        self,
        db: FakeDB,
    ) -> None:
        """Deletes stale message FTS rows before the source rows are cascaded."""
        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "fts cleanup"})
        conversation_id = result["conversation_id"]

        save_message = make_save_message_handler(db)
        await save_message(
            {"conversation_id": conversation_id, "role": "user", "content": "fts row"}
        )

        delete_conversation = make_delete_conversation_handler(db)
        await delete_conversation({"conversation_id": conversation_id})

        row = db.execute("SELECT count(*) FROM messages_fts").fetchone()
        assert row[0] == 0


class TestRecordEmbeddingOperations:
    """Verify handlers that depend on the embedding table."""

    @pytest.mark.asyncio
    async def test_fetch_unembedded_records_without_embedding_table(
        self,
        db: FakeDB,
    ) -> None:
        """Falls back cleanly when an older database has no embedding table."""
        db.execute("DROP TABLE record_embeddings")
        db.commit()

        create_conversation = make_create_conversation_handler(db)
        result = await create_conversation({"title": "legacy db"})
        conversation_id = result["conversation_id"]

        save_record = make_save_record_handler(db)
        await save_record(
            {
                "conversation_id": conversation_id,
                "session_id": "legacy-session",
                "summary": "Keep detailed answers.",
                "key_facts": ["detailed"],
                "source_message_ids": [],
            }
        )

        fetch_unembedded = make_fetch_unembedded_records_handler(db)
        result = await fetch_unembedded({"limit": 10})

        assert len(result["records"]) == 1
        assert result["records"][0]["summary"] == "Keep detailed answers."


class TestCredentialOperations:
    """Verify credential storage and lookup."""

    @pytest.mark.asyncio
    async def test_store_and_get_credential(self, db: FakeDB) -> None:
        """Stores and retrieves a credential for an integration."""
        create_integration = make_create_integration_handler(db)
        result = await create_integration({"name": "github", "service_type": "git"})
        integration_id = result["integration_id"]

        store_credential = make_store_credential_handler(db)
        await store_credential(
            {
                "integration_id": integration_id,
                "credential_type": "api_key",
                "value": "ghp_secret123",
            }
        )

        get_credential = make_get_credential_handler(db)
        result = await get_credential(
            {"integration_id": integration_id, "credential_type": "api_key"}
        )
        assert result["value"] == "ghp_secret123"

    @pytest.mark.asyncio
    async def test_get_nonexistent_credential(self, db: FakeDB) -> None:
        """Returns ``None`` for a missing credential."""
        get_credential = make_get_credential_handler(db)
        result = await get_credential(
            {"integration_id": "none", "credential_type": "api_key"}
        )
        assert result["value"] is None


class TestIntegrationOperations:
    """Verify integration lifecycle handlers."""

    @pytest.mark.asyncio
    async def test_create_list_delete_integration(self, db: FakeDB) -> None:
        """Exercises create, list, and delete for integrations."""
        create_integration = make_create_integration_handler(db)
        await create_integration({"name": "slack", "service_type": "chat"})

        list_integrations = make_list_integrations_handler(db)
        result = await list_integrations(None)
        assert len(result["integrations"]) == 1
        assert result["integrations"][0]["name"] == "slack"

        delete_integration = make_delete_integration_handler(db)
        result = await delete_integration({"name": "slack"})
        assert result["deleted"] is True

        result = await list_integrations(None)
        assert len(result["integrations"]) == 0


class TestAdminOperations:
    """Verify administrative DB handlers."""

    @pytest.mark.asyncio
    async def test_get_stats(self, db: FakeDB) -> None:
        """Returns per-table counts from the stats handler."""
        handler = make_get_stats_handler(db)
        result = await handler(None)
        assert "messages" in result
        assert "conversations" in result
        assert result["messages"] == 0


class TestSigningMaterial:
    """Verify deterministic signing key derivation behavior."""

    @pytest.mark.asyncio
    async def test_get_signing_material(self, db: FakeDB) -> None:
        """Derives key material and salt from the master key."""
        master_key = b"\x01" * 32
        handler = make_get_signing_material_handler(db, master_key)
        result = await handler(None)
        assert "key_material" in result
        assert "salt" in result
        assert len(result["key_material"]) == 64

    @pytest.mark.asyncio
    async def test_signing_material_deterministic(self, db: FakeDB) -> None:
        """Returns stable signing material for the same master key."""
        master_key = b"\x02" * 32
        handler = make_get_signing_material_handler(db, master_key)
        first_result = await handler(None)
        second_result = await handler(None)
        assert first_result["key_material"] == second_result["key_material"]
        assert first_result["salt"] == second_result["salt"]


class TestAllowlistEnforcement:
    """Verify per-channel operation allowlists."""

    def test_socket_a_excludes_credentials(self) -> None:
        """Disallows credential operations on socket A."""
        allowlist = build_allowlist()
        assert not allowlist.is_allowed(CHANNEL_A, "store_credential")
        assert not allowlist.is_allowed(CHANNEL_A, "get_credential")
        assert not allowlist.is_allowed(CHANNEL_A, "delete_credential")

    def test_socket_a_excludes_admin(self) -> None:
        """Disallows admin-only operations on socket A."""
        allowlist = build_allowlist()
        assert not allowlist.is_allowed(CHANNEL_A, "get_signing_material")
        assert not allowlist.is_allowed(CHANNEL_A, "get_stats")

    def test_socket_b_includes_all(self) -> None:
        """Allows all socket-B operations on socket B."""
        allowlist = build_allowlist()
        for operation in SOCKET_B_OPERATIONS:
            assert allowlist.is_allowed(CHANNEL_B, operation), (
                f"Socket B should allow {operation}"
            )

    def test_socket_a_includes_core_operations(self) -> None:
        """Allows core operations on socket A."""
        allowlist = build_allowlist()
        for operation in SOCKET_A_OPERATIONS:
            assert allowlist.is_allowed(CHANNEL_A, operation), (
                f"Socket A should allow {operation}"
            )

    def test_socket_a_includes_scheduled_task_writes(self) -> None:
        """Allows scheduled task writes on socket A for chat-created tasks."""
        allowlist = build_allowlist()
        for operation in SCHEDULED_TASK_SOCKET_A_OPERATIONS:
            assert allowlist.is_allowed(CHANNEL_A, operation), (
                f"Socket A should allow {operation}"
            )

    def test_fetch_unextracted_socket_a_only(self) -> None:
        """S7.1 — extractor eligibility read stays off the web channel."""
        assert "fetch_unextracted_records" in SOCKET_A_OPERATIONS
        assert "fetch_unextracted_records" not in SOCKET_B_OPERATIONS

    def test_socket_b_has_no_graph_writes(self) -> None:
        """S6.1 — graph writes stay off the web-tier channel."""
        graph_writes = {
            "save_entity",
            "save_assertion",
            "invalidate_assertion",
            "prune_graph",
        }
        graph_reads = {
            "get_entity",
            "find_entities",
            "list_assertions_for_entity",
            "find_graph_entry_points",
        }
        assert not graph_writes & SOCKET_B_OPERATIONS
        assert graph_writes <= SOCKET_A_OPERATIONS
        assert graph_reads <= SOCKET_B_OPERATIONS
        assert graph_reads <= SOCKET_A_OPERATIONS

    def test_total_operation_count(self) -> None:
        """Keeps expected operation counts per socket."""
        assert len(SOCKET_A_OPERATIONS) == EXPECTED_SOCKET_A_OPERATION_COUNT
        assert len(SOCKET_B_ONLY_OPERATIONS) == EXPECTED_SOCKET_B_ONLY_OPERATION_COUNT
        assert len(SOCKET_B_OPERATIONS) == EXPECTED_SOCKET_B_OPERATION_COUNT
        assert len(SOCKET_C_OPERATIONS) == EXPECTED_SOCKET_C_OPERATION_COUNT


class TestRegistryIntegration:
    """Verify registry dispatch end-to-end."""

    @pytest.mark.asyncio
    async def test_dispatch_save_message(self, db: FakeDB) -> None:
        """Dispatches ``save_message`` through the registry."""
        registry = build_registry(db, "/tmp/test.db", b"\x00" * 32)
        operation = DBOperation(
            request_id="r1",
            operation="save_message",
            parameters={
                "conversation_id": "c1",
                "role": "user",
                "content": "hello",
            },
        )
        result = await registry.dispatch(operation)
        assert result.status == "success"
        assert "message_id" in result.data

    @pytest.mark.asyncio
    async def test_dispatch_unknown_operation(self, db: FakeDB) -> None:
        """Rejects unknown operations via dispatch."""
        registry = build_registry(db, "/tmp/test.db", b"\x00" * 32)
        operation = DBOperation(
            request_id="r1",
            operation="drop_table",
            parameters={},
        )
        result = await registry.dispatch(operation)
        assert result.status == "error"
        assert "Unknown operation" in result.error_message

    @pytest.mark.asyncio
    async def test_dispatch_invalid_params(self, db: FakeDB) -> None:
        """Rejects invalid parameters via dispatch."""
        registry = build_registry(db, "/tmp/test.db", b"\x00" * 32)
        operation = DBOperation(
            request_id="r1",
            operation="save_message",
            parameters={"bad_field": "value"},
        )
        result = await registry.dispatch(operation)
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_health_check(self, db: FakeDB) -> None:
        """Dispatches the built-in health-check operation."""
        registry = build_registry(db, "/tmp/test.db", b"\x00" * 32)
        operation = DBOperation(
            request_id="r1",
            operation="_health_check",
        )
        result = await registry.dispatch(operation)
        assert result.status == "success"
        assert result.data["db_connected"] is True
