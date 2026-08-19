"""Tests for DatabaseManager including encryption and CRUD operations.

Covers database creation with encryption, invalid key handling, sentinel
validation, basic CRUD operations, and connection lifecycle.
"""

import os
import secrets

import pytest

from backend.security.database import (
    DatabaseError,
    DatabaseKeyError,
    DatabaseManager,
)
from backend.security.schema import initialize_schema


@pytest.fixture
def master_key():
    """Generate a test master key."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db_path(tmp_path):
    """Return a path for a test database."""
    return str(tmp_path / "test.db")


@pytest.fixture
def db(db_path, master_key):
    """Create and return an open DatabaseManager."""
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


class TestDatabaseCreation:
    """Tests for database creation and opening."""

    def test_open_creates_new_database(self, db_path, master_key):
        """Verify opening a new path creates the database file."""
        manager = DatabaseManager(db_path)
        manager.open(master_key)
        assert os.path.exists(db_path)
        manager.close()

    def test_is_open_after_open(self, db_path, master_key):
        """Verify is_open returns True after opening."""
        manager = DatabaseManager(db_path)
        manager.open(master_key)
        assert manager.is_open is True
        manager.close()

    def test_is_open_false_initially(self, db_path):
        """Verify is_open returns False before opening."""
        manager = DatabaseManager(db_path)
        assert manager.is_open is False

    def test_is_open_false_after_close(self, db_path, master_key):
        """Verify is_open returns False after closing."""
        manager = DatabaseManager(db_path)
        manager.open(master_key)
        manager.close()
        assert manager.is_open is False

    def test_rejects_short_master_key_before_open(self, db_path):
        """Verify malformed raw keys are rejected before opening SQLCipher."""
        manager = DatabaseManager(db_path)

        with pytest.raises(DatabaseError, match="32 bytes"):
            manager.open(bytearray(b"short"))

    def test_raw_key_hex_accepts_bytes_and_bytearray(self):
        """Verify the raw key helper accepts both supported bytes-like types."""
        raw = b"x" * 32

        assert DatabaseManager._raw_key_hex(raw) == raw.hex()
        assert DatabaseManager._raw_key_hex(bytearray(raw)) == raw.hex()

    def test_raw_key_hex_rejects_non_bytes(self):
        """Verify the raw key helper rejects non bytes-like input."""
        with pytest.raises(DatabaseError, match="bytes-like"):
            DatabaseManager._raw_key_hex("not-bytes")  # type: ignore[arg-type]


class TestDatabaseEncryption:
    """Tests for database encryption with wrong keys."""

    def test_wrong_key_raises(self, db_path, master_key):
        """Verify opening with wrong key raises DatabaseKeyError."""
        manager = DatabaseManager(db_path)
        manager.open(master_key)
        manager.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        manager.commit()
        manager.close()

        wrong_key = bytearray(secrets.token_bytes(32))
        manager2 = DatabaseManager(db_path)
        with pytest.raises(DatabaseKeyError):
            manager2.open(wrong_key)


class TestSentinelValidation:
    """Tests for sentinel query validation."""

    def test_sentinel_passes_with_correct_key(self, db):
        """Verify sentinel query succeeds with correct key."""
        cursor = db.execute("SELECT count(*) FROM sqlite_master")
        count = cursor.fetchone()[0]
        assert count >= 0


class TestCRUDOperations:
    """Tests for basic CRUD on the database."""

    def test_insert_and_select(self, db):
        """Verify basic insert and select operations."""
        db.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)",
            ("conv-1", "Test"),
        )
        db.commit()
        cursor = db.execute(
            "SELECT id, title FROM conversations WHERE id = ?",
            ("conv-1",),
        )
        row = cursor.fetchone()
        assert row[0] == "conv-1"
        assert row[1] == "Test"

    def test_cascade_delete(self, db):
        """Verify cascade deletion from conversations to messages."""
        db.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)",
            ("conv-2", "Delete Test"),
        )
        db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            ("msg-1", "conv-2", "user", "Hello"),
        )
        db.commit()

        db.execute("DELETE FROM conversations WHERE id = ?", ("conv-2",))
        db.commit()

        cursor = db.execute(
            "SELECT count(*) FROM messages WHERE conversation_id = ?", ("conv-2",)
        )
        assert cursor.fetchone()[0] == 0

    def test_execute_without_open_raises(self, db_path):
        """Verify execute on closed database raises DatabaseError."""
        manager = DatabaseManager(db_path)
        with pytest.raises(DatabaseError, match="not open"):
            manager.execute("SELECT 1")

    def test_commit_without_open_raises(self, db_path):
        """Verify commit on closed database raises DatabaseError."""
        manager = DatabaseManager(db_path)
        with pytest.raises(DatabaseError, match="not open"):
            manager.commit()

    def test_executemany(self, db):
        """Verify executemany inserts multiple rows."""
        rows = [
            ("conv-a", "Title A"),
            ("conv-b", "Title B"),
        ]
        db.executemany("INSERT INTO conversations (id, title) VALUES (?, ?)", rows)
        db.commit()
        cursor = db.execute("SELECT count(*) FROM conversations")
        assert cursor.fetchone()[0] == 2

    def test_executemany_without_open_raises(self, db_path):
        """Verify executemany on closed database raises DatabaseError."""
        manager = DatabaseManager(db_path)
        with pytest.raises(DatabaseError, match="not open"):
            manager.executemany("INSERT INTO test VALUES (?)", [("a",)])


class TestMetadataTable:
    """Tests for metadata table creation and BLOB storage."""

    def test_metadata_table_created_by_initialize_schema(self, db):
        """Verify initialize_schema creates the metadata table."""
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'"
        )
        assert cursor.fetchone() is not None

    def test_metadata_blob_round_trip(self, db):
        """Verify BLOB values survive insert/select round-trip."""
        raw = os.urandom(16)
        db.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            ("test_key", raw),
        )
        db.commit()
        row = db.execute("SELECT value FROM metadata WHERE key = 'test_key'").fetchone()
        assert row[0] == raw


class TestSchemaDoubleInit:
    """Tests for idempotent schema initialization."""

    def test_double_init_is_idempotent(self, db):
        """Verify calling initialize_schema twice does not error."""
        initialize_schema(db)
        cursor = db.execute("SELECT count(*) FROM schema_version")
        assert cursor.fetchone()[0] == 1


class TestCloseIdempotent:
    """Tests for close() edge cases."""

    def test_close_when_already_closed(self, db_path):
        """Verify close on already-closed database is safe."""
        manager = DatabaseManager(db_path)
        manager.close()
        assert manager.is_open is False


class TestSentinelFailure:
    """Tests for sentinel validation failure path."""

    def test_sentinel_failure_raises_key_error(self, tmp_path):
        """Verify sentinel validation failure raises DatabaseKeyError."""
        from unittest.mock import MagicMock, patch

        db_path = str(tmp_path / "sentinel.db")
        manager = DatabaseManager(db_path)
        master_key = bytearray(secrets.token_bytes(32))

        mock_conn = MagicMock()
        call_count = 0

        def fake_execute(sql, *args):
            nonlocal call_count
            call_count += 1
            if "sqlite_master" in sql:
                raise Exception("sentinel failed")
            return MagicMock()

        mock_conn.execute = fake_execute

        with (
            patch("sqlcipher3.connect", return_value=mock_conn),
            pytest.raises(DatabaseKeyError),
        ):
            manager.open(master_key)

    def test_generic_open_error_raises_database_error(self, tmp_path):
        """Verify non-key errors raise generic DatabaseError."""
        from unittest.mock import patch

        db_path = str(tmp_path / "generic.db")
        manager = DatabaseManager(db_path)
        master_key = bytearray(secrets.token_bytes(32))

        with (
            patch(
                "sqlcipher3.connect",
                side_effect=Exception("connection pool exhausted"),
            ),
            pytest.raises(DatabaseError, match="connection pool"),
        ):
            manager.open(master_key)
