"""Tests for persisted file context tables and DB proxy handlers."""

import asyncio
import secrets

import pytest

from backend.processes.db_proxy.handlers.file import (
    make_delete_file_metadata_handler,
    make_get_file_context_handler,
    make_save_file_context_handler,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


@pytest.fixture
def master_key() -> bytearray:
    """Return a random master key for one test database."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "file-context.db")


@pytest.fixture
def db(db_path: str, master_key: bytearray) -> DatabaseManager:
    """Return an initialized database manager."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


class TestFileContextSchema:
    """Tests for file-context schema initialization."""

    def test_initialize_schema_creates_file_context_tables(self, db: DatabaseManager):
        """Verify file context tables exist after schema initialization."""
        table_names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "file_contexts" in table_names
        assert "file_context_chunks" in table_names


class TestFileContextHandlers:
    """Tests for persisted file-context handler round-trips."""

    def test_save_and_get_file_context_round_trip(self, db: DatabaseManager):
        """Verify stored file context returns the saved summary and chunks."""
        db.execute(
            "INSERT INTO files (id, original_name, size, mime_type) "
            "VALUES (?, ?, ?, ?)",
            ("file-1", "runbook.md", 4096, "text/markdown"),
        )
        db.commit()
        save_handler = make_save_file_context_handler(db)
        get_handler = make_get_file_context_handler(db)

        asyncio.run(
            save_handler(
                {
                    "file_id": "file-1",
                    "file_kind": "markup",
                    "estimated_tokens": 2200,
                    "processing_mode": "summarized",
                    "processing_reason": "File exceeded preview limit.",
                    "summary_overview": "runbook.md is a markup file.",
                    "summary_structure": ["# Overview", "## Recovery"],
                    "summary_key_points": ["Document the rollback steps."],
                    "summary_warnings": ["Large file was summarized."],
                    "chunks": [
                        {
                            "chunk_id": "file-1:0",
                            "chunk_index": 0,
                            "text": "Rollback steps start here.",
                            "line_start": 1,
                            "line_end": 12,
                            "estimated_tokens": 120,
                        }
                    ],
                }
            )
        )

        result = asyncio.run(get_handler({"file_id": "file-1"}))
        assert result["file_context"] is not None
        assert result["file_context"]["processing_reason"] == (
            "File exceeded preview limit."
        )
        assert result["file_context"]["summary_overview"] == (
            "runbook.md is a markup file."
        )
        assert result["file_context"]["summary_structure"] == [
            "# Overview",
            "## Recovery",
        ]
        assert result["file_context"]["chunks"] == [
            {
                "chunk_id": "file-1:0",
                "chunk_index": 0,
                "text": "Rollback steps start here.",
                "line_start": 1,
                "line_end": 12,
                "estimated_tokens": 120,
            }
        ]

    def test_delete_file_metadata_cascades_to_file_context(self, db: DatabaseManager):
        """Verify deleting file metadata removes stored context and chunks."""
        db.execute(
            "INSERT INTO files (id, original_name, size, mime_type) "
            "VALUES (?, ?, ?, ?)",
            ("file-2", "runbook.md", 4096, "text/markdown"),
        )
        db.execute(
            "INSERT INTO file_contexts ("
            "file_id, file_kind, estimated_tokens, processing_mode, "
            "processing_reason"
            ") VALUES (?, ?, ?, ?, ?)",
            ("file-2", "markup", 2200, "summarized", "Stored as summary."),
        )
        db.execute(
            "INSERT INTO file_context_chunks ("
            "id, file_id, chunk_index, text, estimated_tokens"
            ") VALUES (?, ?, ?, ?, ?)",
            ("file-2:0", "file-2", 0, "chunk body", 20),
        )
        db.commit()
        delete_handler = make_delete_file_metadata_handler(db)

        asyncio.run(delete_handler({"file_id": "file-2"}))

        context_count = db.execute(
            "SELECT count(*) FROM file_contexts WHERE file_id = ?",
            ("file-2",),
        ).fetchone()[0]
        chunk_count = db.execute(
            "SELECT count(*) FROM file_context_chunks WHERE file_id = ?",
            ("file-2",),
        ).fetchone()[0]
        assert context_count == 0
        assert chunk_count == 0
