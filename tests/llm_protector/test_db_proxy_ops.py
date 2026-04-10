"""Tests for DB Proxy process operations.

Covers named operations, parameter validation, allowlist
enforcement, and signing key derivation. Uses an in-memory SQLite
database as a stand-in for the encrypted database.
"""

import sqlite3

import pytest
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
    make_create_conversation_handler,
    make_create_integration_handler,
    make_delete_conversation_handler,
    make_delete_integration_handler,
    make_fetch_unembedded_records_handler,
    make_get_credential_handler,
    make_get_memory_route_diagnostics_summary_handler,
    make_get_signing_material_handler,
    make_get_stats_handler,
    make_list_conversations_handler,
    make_list_integrations_handler,
    make_list_memory_route_diagnostics_handler,
    make_list_summaries_handler,
    make_load_context_handler,
    make_load_recent_messages_handler,
    make_mark_tainted_handler,
    make_save_message_handler,
    make_save_record_handler,
    make_save_summary_handler,
    make_store_credential_handler,
    make_update_conversation_processing_state_handler,
    make_update_conversation_title_handler,
    make_verify_chain_handler,
    make_write_audit_handler,
    make_write_memory_route_diagnostic_handler,
)
from backend.security.diagnostics_schema import (
    initialize_diagnostics_schema,
    prune_memory_route_diagnostics,
)
from pydantic import ValidationError


EXPECTED_SOCKET_A_OPERATION_COUNT = 67
EXPECTED_SOCKET_B_ONLY_OPERATION_COUNT = 43
EXPECTED_SOCKET_B_OPERATION_COUNT = 64
EXPECTED_SOCKET_C_OPERATION_COUNT = 14


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
            """CREATE TABLE summaries (
                id TEXT PRIMARY KEY,
                main_point TEXT NOT NULL,
                based_on TEXT NOT NULL DEFAULT '[]',
                trust_level TEXT NOT NULL DEFAULT 'medium',
                summary_scope TEXT NOT NULL DEFAULT 'long_term',
                trust_level_score INTEGER NOT NULL DEFAULT 2,
                source_record_ids TEXT NOT NULL DEFAULT '[]',
                record_relations TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
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
            """CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts
               USING fts5(main_point, based_on, content_rowid='rowid')""",
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
    """Verify ``load_context`` handler behavior."""

    @pytest.mark.asyncio
    async def test_load_context_combined(self, db: FakeDB) -> None:
        """Returns summaries, records, and messages together."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.execute(
            "INSERT INTO summaries (id, main_point, trust_level) VALUES (?, ?, ?)",
            ("r1", "user prefers Python", 0.9),
        )
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary) "
            "VALUES (?, ?, ?, ?)",
            ("o1", "s1", "s1", "discussed async"),
        )
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("m1", "s1", "user", "hello"),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler({"session_id": "s1", "conversation_id": "s1"})
        assert len(result["summaries"]) == 1
        assert result["summaries"][0]["main_point"] == "user prefers Python"
        assert "created_at" in result["summaries"][0]
        assert len(result["summaries"][0]["created_at"]) > 0
        assert len(result["records"]) == 1
        assert "created_at" in result["records"][0]
        assert len(result["records"][0]["created_at"]) > 0
        assert len(result["messages"]) == 1

    @pytest.mark.asyncio
    async def test_load_context_without_query_no_search_results(
        self,
        db: FakeDB,
    ) -> None:
        """Omits search-related fields when no query is provided."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler({"session_id": "s1", "conversation_id": "s1"})
        assert "search_results" not in result

    @pytest.mark.asyncio
    async def test_load_context_accepts_query_param(self, db: FakeDB) -> None:
        """Accepts a query and returns search-related sections."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary) "
            "VALUES (?, ?, ?, ?)",
            ("o1", "s1", "s1", "test record"),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "s1",
                "query": "test",
            }
        )
        assert "search_results" in result
        assert "message_search_results" in result
        assert "summary_search_results" in result
        assert "retrieved_messages" in result

    @pytest.mark.asyncio
    async def test_load_context_summary_search_reorders_results(
        self,
        db: FakeDB,
    ) -> None:
        """Places summary keyword hits ahead of passive trust ordering."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("r1", "General preference", '["misc"]', "high", 3, '["o1"]'),
        )
        db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, trust_level_score, "
            "source_record_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "r2",
                "User prefers concise Russian answers",
                '["Asked for Russian replies"]',
                "medium",
                2,
                '["o2"]',
            ),
        )
        first_row_id = db.execute(
            "SELECT rowid FROM summaries WHERE id = 'r1'"
        ).fetchone()[0]
        second_row_id = db.execute(
            "SELECT rowid FROM summaries WHERE id = 'r2'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO summaries_fts (rowid, main_point, based_on) VALUES (?, ?, ?)",
            (first_row_id, "general prefer", "misc"),
        )
        db.execute(
            "INSERT INTO summaries_fts (rowid, main_point, based_on) VALUES (?, ?, ?)",
            (
                second_row_id,
                "user prefer concis russian answer",
                "ask for russian repli",
            ),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "s1",
                "query": "Russian",
            }
        )
        assert "summary_search_results" in result
        assert (
            result["summaries"][0]["main_point"]
            == "User prefers concise Russian answers"
        )

    @pytest.mark.asyncio
    async def test_load_context_message_search(self, db: FakeDB) -> None:
        """Searches indexed messages when a query is provided."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s2",))
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("m1", "s1", "user", "hello world"),
        )
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("m2", "s2", "user", "hello universe"),
        )
        first_row_id = db.execute(
            "SELECT rowid FROM messages WHERE id = 'm1'"
        ).fetchone()[0]
        second_row_id = db.execute(
            "SELECT rowid FROM messages WHERE id = 'm2'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
            (first_row_id, "hello world"),
        )
        db.execute(
            "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
            (second_row_id, "hello universe"),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "s1",
                "query": "hello",
            }
        )
        assert "message_search_results" in result
        assert len(result["message_search_results"]) > 0
        assert "retrieved_messages" in result

    @pytest.mark.asyncio
    async def test_load_context_memory_query_reweights_search_layers(
        self,
        db: FakeDB,
    ) -> None:
        """Memory queries should favor semantic layers over raw message hits."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        for index in range(5):
            db.execute("INSERT INTO conversations (id) VALUES (?)", (f"c{index}",))
            db.execute(
                "INSERT INTO messages (id, conversation_id, role, content) "
                "VALUES (?, ?, ?, ?)",
                (
                    f"m{index}",
                    f"c{index}",
                    "assistant",
                    f"alpha memory detail {index}",
                ),
            )
            db.execute(
                "INSERT INTO records "
                "(id, conversation_id, session_id, summary) "
                "VALUES (?, ?, ?, ?)",
                (
                    f"o{index}",
                    "s1",
                    "s1",
                    f"alpha semantic memory {index}",
                ),
            )
            db.execute(
                "INSERT INTO summaries "
                "("
                "id, main_point, based_on, trust_level, trust_level_score, "
                "source_record_ids"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"r{index}",
                    f"alpha long-term main point {index}",
                    '["alpha reason"]',
                    "medium",
                    2,
                    f'["o{index}"]',
                ),
            )

            message_row_id = db.execute(
                "SELECT rowid FROM messages WHERE id = ?",
                (f"m{index}",),
            ).fetchone()[0]
            record_row_id = db.execute(
                "SELECT rowid FROM records WHERE id = ?",
                (f"o{index}",),
            ).fetchone()[0]
            summary_row_id = db.execute(
                "SELECT rowid FROM summaries WHERE id = ?",
                (f"r{index}",),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
                (message_row_id, f"alpha memory detail {index}"),
            )
            db.execute(
                "INSERT INTO records_fts (rowid, summary, key_facts) VALUES (?, ?, ?)",
                (record_row_id, f"alpha semantic memory {index}", "[]"),
            )
            db.execute(
                "INSERT INTO summaries_fts (rowid, main_point, based_on) "
                "VALUES (?, ?, ?)",
                (summary_row_id, f"alpha long-term main point {index}", "alpha"),
            )
        db.commit()

        handler = make_load_context_handler(db)
        regular_result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "s1",
                "query": "alpha",
                "record_limit": 2,
                "summary_limit": 1,
                "message_search_limit": 6,
            }
        )
        memory_result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "s1",
                "query": "\u043f\u043e\u043c\u043d\u0438\u0448\u044c alpha",
                "record_limit": 2,
                "summary_limit": 1,
                "message_search_limit": 6,
            }
        )

        assert len(memory_result["search_results"]) > len(
            regular_result["search_results"]
        )
        assert len(memory_result["summary_search_results"]) > len(
            regular_result["summary_search_results"]
        )
        assert len(memory_result["message_search_results"]) < len(
            regular_result["message_search_results"]
        )

    @pytest.mark.asyncio
    async def test_load_context_message_dedup(self, db: FakeDB) -> None:
        """Excludes chronological messages from retrieved search hits."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("s1",))
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("m1", "s1", "user", "specific keyword searchable"),
        )
        first_row_id = db.execute(
            "SELECT rowid FROM messages WHERE id = 'm1'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
            (first_row_id, "specific keyword searchable"),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "s1",
                "query": "specific keyword",
            }
        )
        retrieved_ids = [
            retrieved_message["id"]
            for retrieved_message in result["retrieved_messages"]
        ]
        assert "m1" not in retrieved_ids

    @pytest.mark.asyncio
    async def test_load_context_skips_retrieved_memory_meta_messages(
        self,
        db: FakeDB,
    ) -> None:
        """Recall queries should not re-inject old memory-meta questions."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("current",))
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("other",))
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("m1", "other", "user", "I use Ollama for local model testing."),
        )
        db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            (
                "m2",
                "other",
                "user",
                "What do you remember about our discussion of Ollama?",
            ),
        )
        first_row_id = db.execute(
            "SELECT rowid FROM messages WHERE id = 'm1'"
        ).fetchone()[0]
        second_row_id = db.execute(
            "SELECT rowid FROM messages WHERE id = 'm2'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
            (first_row_id, "I use Ollama for local model testing."),
        )
        db.execute(
            "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
            (second_row_id, "What do you remember about our discussion of Ollama?"),
        )
        db.commit()

        handler = make_load_context_handler(db)
        result = await handler(
            {
                "session_id": "current",
                "conversation_id": "current",
                "query": "What do you remember about our discussion of Ollama?",
                "message_search_limit": 10,
            }
        )
        retrieved_ids = [
            retrieved_message["id"]
            for retrieved_message in result["retrieved_messages"]
        ]
        assert "m1" in retrieved_ids
        assert "m2" not in retrieved_ids

    @pytest.mark.asyncio
    async def test_write_memory_route_diagnostic(self, db: FakeDB) -> None:
        """Stores one structured route diagnostic with signal rows."""
        initialize_diagnostics_schema(db)
        handler = make_write_memory_route_diagnostic_handler(db)

        result = await handler(
            {
                "session_id": "s1",
                "conversation_id": "c1",
                "message_id": "m1",
                "channel": "web",
                "initial_intent": "non_memory_question",
                "initial_confidence": "none",
                "initial_role_filter": None,
                "initial_source": "signal",
                "initial_signal_groups": [],
                "fallback_attempted": True,
                "fallback_reason": "unsupported_language_question_shape",
                "fallback_intent": "memory_recall_question",
                "fallback_confidence": "high",
                "fallback_role_filter": None,
                "fallback_source": "semantic_fallback",
                "final_intent": "memory_recall_question",
                "final_confidence": "high",
                "final_role_filter": None,
                "final_source": "semantic_fallback",
                "final_signal_groups": ["general_memory_recall_signals"],
                "route_mismatch": True,
                "mismatch_label": "intent_changed",
                "used_long_term_mode": False,
            }
        )

        diagnostic_id = result["diagnostic_id"]
        row = db.execute(
            "SELECT session_id, fallback_reason, final_intent, mismatch_label "
            "FROM memory_route_diagnostics WHERE id = ?",
            (diagnostic_id,),
        ).fetchone()
        assert row == (
            "s1",
            "unsupported_language_question_shape",
            "memory_recall_question",
            "intent_changed",
        )
        signal_rows = db.execute(
            "SELECT stage, signal_group "
            "FROM memory_route_signal_diagnostics "
            "WHERE diagnostic_id = ?",
            (diagnostic_id,),
        ).fetchall()
        assert signal_rows == [("final", "general_memory_recall_signals")]

    @pytest.mark.asyncio
    async def test_list_memory_route_diagnostics(self, db: FakeDB) -> None:
        """Lists structured route diagnostics with grouped signal rows."""
        initialize_diagnostics_schema(db)
        writer = make_write_memory_route_diagnostic_handler(db)
        reader = make_list_memory_route_diagnostics_handler(db)

        await writer(
            {
                "session_id": "s1",
                "conversation_id": "c1",
                "message_id": "m1",
                "channel": "web",
                "initial_intent": "non_memory_question",
                "initial_confidence": "none",
                "initial_role_filter": None,
                "initial_source": "signal",
                "initial_signal_groups": [],
                "fallback_attempted": True,
                "fallback_reason": "unsupported_language_question_shape",
                "fallback_intent": "user_recall_question",
                "fallback_confidence": "high",
                "fallback_role_filter": "user",
                "fallback_source": "semantic_fallback",
                "final_intent": "user_recall_question",
                "final_confidence": "high",
                "final_role_filter": "user",
                "final_source": "semantic_fallback",
                "final_signal_groups": ["user_recall_signals"],
                "route_mismatch": True,
                "mismatch_label": "intent_changed",
                "used_long_term_mode": False,
            }
        )

        result = await reader(
            {
                "limit": 10,
                "offset": 0,
                "mismatch_only": True,
            }
        )

        assert len(result["diagnostics"]) == 1
        diagnostic = result["diagnostics"][0]
        assert diagnostic["session_id"] == "s1"
        assert diagnostic["fallback"]["attempted"] is True
        assert diagnostic["fallback"]["reason"] == (
            "unsupported_language_question_shape"
        )
        assert diagnostic["final_route"]["intent"] == "user_recall_question"
        assert diagnostic["final_route"]["signal_groups"] == ["user_recall_signals"]
        assert diagnostic["mismatch_label"] == "intent_changed"

    @pytest.mark.asyncio
    async def test_get_memory_route_diagnostics_summary(self, db: FakeDB) -> None:
        """Aggregates route diagnostics into counts and breakdowns."""
        initialize_diagnostics_schema(db)
        writer = make_write_memory_route_diagnostic_handler(db)
        summary_reader = make_get_memory_route_diagnostics_summary_handler(db)

        await writer(
            {
                "session_id": "s1",
                "conversation_id": "c1",
                "message_id": "m1",
                "channel": "web",
                "initial_intent": "non_memory_question",
                "initial_confidence": "none",
                "initial_role_filter": None,
                "initial_source": "signal",
                "initial_signal_groups": [],
                "fallback_attempted": True,
                "fallback_reason": "unsupported_language_question_shape",
                "fallback_intent": "user_recall_question",
                "fallback_confidence": "high",
                "fallback_role_filter": "user",
                "fallback_source": "semantic_fallback",
                "final_intent": "user_recall_question",
                "final_confidence": "high",
                "final_role_filter": "user",
                "final_source": "semantic_fallback",
                "final_signal_groups": ["user_recall_signals"],
                "route_mismatch": True,
                "mismatch_label": "intent_changed",
                "used_long_term_mode": False,
            }
        )
        await writer(
            {
                "session_id": "s2",
                "conversation_id": "c2",
                "message_id": "m2",
                "channel": "web",
                "initial_intent": "long_term_memory_question",
                "initial_confidence": "high",
                "initial_role_filter": None,
                "initial_source": "signal",
                "initial_signal_groups": ["long_term_memory_signals"],
                "fallback_attempted": False,
                "final_intent": "long_term_memory_question",
                "final_confidence": "high",
                "final_role_filter": None,
                "final_source": "signal",
                "final_signal_groups": ["long_term_memory_signals"],
                "route_mismatch": False,
                "mismatch_label": "no_mismatch",
                "used_long_term_mode": True,
            }
        )

        result = await summary_reader(None)

        assert result["totals"] == {
            "total_rows": 2,
            "fallback_rows": 1,
            "mismatch_rows": 1,
            "long_term_rows": 1,
        }
        assert result["mismatch_labels"] == [
            {"label": "intent_changed", "count": 1},
            {"label": "no_mismatch", "count": 1},
        ]
        assert result["final_intents"] == [
            {"intent": "long_term_memory_question", "count": 1},
            {"intent": "user_recall_question", "count": 1},
        ]
        assert result["fallback_reasons"] == [
            {
                "reason": "unsupported_language_question_shape",
                "count": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_get_memory_route_diagnostics_summary_recent_hours(
        self,
        db: FakeDB,
    ) -> None:
        """Recent-hours summary should only include the requested time window."""
        initialize_diagnostics_schema(db)
        db.execute(
            """
            INSERT INTO memory_route_diagnostics (
                created_at,
                session_id,
                conversation_id,
                message_id,
                channel,
                initial_intent,
                initial_confidence,
                initial_source,
                fallback_attempted,
                fallback_reason,
                final_intent,
                final_confidence,
                final_source,
                route_mismatch,
                mismatch_label,
                used_long_term_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2000-01-01T00:00:00.000",
                "old",
                "c-old",
                "m-old",
                "web",
                "non_memory_question",
                "none",
                "signal",
                1,
                "unsupported_language_question_shape",
                "user_recall_question",
                "high",
                "semantic_fallback",
                1,
                "intent_changed",
                0,
            ),
        )
        db.execute(
            """
            INSERT INTO memory_route_diagnostics (
                session_id,
                conversation_id,
                message_id,
                channel,
                initial_intent,
                initial_confidence,
                initial_source,
                fallback_attempted,
                fallback_reason,
                final_intent,
                final_confidence,
                final_source,
                route_mismatch,
                mismatch_label,
                used_long_term_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "recent",
                "c-recent",
                "m-recent",
                "web",
                "non_memory_question",
                "none",
                "signal",
                1,
                "unsupported_language_question_shape",
                "user_recall_question",
                "high",
                "semantic_fallback",
                1,
                "intent_changed",
                0,
            ),
        )
        db.commit()

        summary_reader = make_get_memory_route_diagnostics_summary_handler(db)
        result = await summary_reader({"recent_hours": 24})

        assert result["recent_hours"] == 24
        assert result["totals"] == {
            "total_rows": 1,
            "fallback_rows": 1,
            "mismatch_rows": 1,
            "long_term_rows": 0,
        }

    @pytest.mark.asyncio
    async def test_write_memory_route_diagnostic_prunes_expired_rows(
        self,
        db: FakeDB,
    ) -> None:
        """Writing a new diagnostic should prune records older than retention."""
        initialize_diagnostics_schema(db)
        db.execute(
            """
            INSERT INTO memory_route_diagnostics (
                created_at,
                session_id,
                conversation_id,
                message_id,
                channel,
                initial_intent,
                initial_confidence,
                initial_source,
                final_intent,
                final_confidence,
                final_source,
                mismatch_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2000-01-01T00:00:00.000",
                "old-session",
                "old-conversation",
                "old-message",
                "web",
                "non_memory_question",
                "none",
                "signal",
                "non_memory_question",
                "none",
                "signal",
                "no_mismatch",
            ),
        )
        db.commit()

        writer = make_write_memory_route_diagnostic_handler(db, retention_days=14)
        await writer(
            {
                "session_id": "s1",
                "conversation_id": "c1",
                "message_id": "m1",
                "channel": "web",
                "initial_intent": "non_memory_question",
                "initial_confidence": "none",
                "initial_role_filter": None,
                "initial_source": "signal",
                "initial_signal_groups": [],
                "fallback_attempted": False,
                "final_intent": "non_memory_question",
                "final_confidence": "none",
                "final_role_filter": None,
                "final_source": "signal",
                "final_signal_groups": [],
                "route_mismatch": False,
                "mismatch_label": "no_mismatch",
                "used_long_term_mode": False,
            }
        )

        remaining_sessions = db.execute(
            "SELECT session_id FROM memory_route_diagnostics ORDER BY id ASC"
        ).fetchall()
        assert remaining_sessions == [("s1",)]

    def test_prune_memory_route_diagnostics_deletes_expired_rows(
        self,
        db: FakeDB,
    ) -> None:
        """Explicit diagnostics cleanup should remove rows older than retention."""
        initialize_diagnostics_schema(db)
        db.execute(
            """
            INSERT INTO memory_route_diagnostics (
                created_at,
                session_id,
                conversation_id,
                message_id,
                channel,
                initial_intent,
                initial_confidence,
                initial_source,
                final_intent,
                final_confidence,
                final_source,
                mismatch_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2000-01-01T00:00:00.000",
                "expired",
                "c1",
                "m1",
                "web",
                "non_memory_question",
                "none",
                "signal",
                "non_memory_question",
                "none",
                "signal",
                "no_mismatch",
            ),
        )
        db.execute(
            """
            INSERT INTO memory_route_diagnostics (
                created_at,
                session_id,
                conversation_id,
                message_id,
                channel,
                initial_intent,
                initial_confidence,
                initial_source,
                final_intent,
                final_confidence,
                final_source,
                mismatch_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2999-01-01T00:00:00.000",
                "fresh",
                "c2",
                "m2",
                "web",
                "non_memory_question",
                "none",
                "signal",
                "non_memory_question",
                "none",
                "signal",
                "no_mismatch",
            ),
        )
        db.commit()

        deleted_count = prune_memory_route_diagnostics(db, retention_days=14)

        assert deleted_count == 1
        remaining_sessions = db.execute(
            "SELECT session_id FROM memory_route_diagnostics ORDER BY id ASC"
        ).fetchall()
        assert remaining_sessions == [("fresh",)]


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
    async def test_mark_tainted(self, db: FakeDB) -> None:
        """Marks an record as tainted."""
        db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        db.execute(
            "INSERT INTO records (id, conversation_id, session_id, summary) "
            "VALUES (?, ?, ?, ?)",
            ("o1", "c1", "s1", "test"),
        )
        db.commit()

        handler = make_mark_tainted_handler(db)
        await handler({"record_id": "o1"})

        row = db.execute(
            "SELECT tainted FROM records WHERE id = ?",
            ("o1",),
        ).fetchone()
        assert row[0] == 1


class TestSummaryOperations:
    """Verify summary storage and listing."""

    @pytest.mark.asyncio
    async def test_save_and_list_summaries(self, db: FakeDB) -> None:
        """Saves a summary and returns it from the list handler."""
        save_summary = make_save_summary_handler(db)
        result = await save_summary(
            {
                "main_point": "user likes concise answers",
                "trust_level": "high",
                "summary_scope": "short_term",
            }
        )
        assert "summary_id" in result

        list_summaries = make_list_summaries_handler(db)
        result = await list_summaries({"limit": 10})
        assert len(result["summaries"]) == 1
        assert result["summaries"][0]["main_point"] == "user likes concise answers"
        assert result["summaries"][0]["scope"] == "short_term"

    @pytest.mark.asyncio
    async def test_save_summary_persists_typed_record_relations(
        self,
        db: FakeDB,
    ) -> None:
        """Typed record relations should persist alongside legacy IDs."""
        save_summary = make_save_summary_handler(db)
        result = await save_summary(
            {
                "main_point": "user changed preference",
                "trust_level": "medium",
                "summary_scope": "long_term",
                "source_record_ids": ["o1"],
                "record_relations": [
                    {
                        "record_id": "o1",
                        "relation": "conflicts",
                    }
                ],
            }
        )

        row = db.execute(
            "SELECT summary_scope, source_record_ids, record_relations "
            "FROM summaries WHERE id = ?",
            (result["summary_id"],),
        ).fetchone()
        assert row is not None
        assert row[0] == "long_term"
        assert row[1] == '["o1"]'
        assert row[2] == '[{"record_id": "o1", "relation": "conflicts"}]'


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
