"""Tests for audit log with SHA-256 hash chain.

Covers entry creation, hash chain integrity verification, anchor
logging, and tamper detection.
"""

import secrets

import pytest

from backend.security.audit import (
    append_audit_entry,
    initialize_audit_schema,
    verify_chain,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


@pytest.fixture
def audit_db(tmp_path):
    """Create an open database with audit schema."""
    db = DatabaseManager(str(tmp_path / "audit.db"))
    db.open(bytearray(secrets.token_bytes(32)))
    initialize_schema(db)
    initialize_audit_schema(db)
    yield db
    db.close()


class TestAppendEntry:
    """Tests for appending audit log entries."""

    def test_append_returns_id(self, audit_db):
        """Verify append returns a positive entry ID."""
        entry_id = append_audit_entry(audit_db, event_type="test_event")
        assert entry_id > 0

    def test_append_with_all_fields(self, audit_db):
        """Verify append works with all optional fields."""
        entry_id = append_audit_entry(
            audit_db,
            event_type="tool_call",
            tool_name="file_read",
            parameters='{"path": "/data/file.txt"}',
            result="success",
            zone="GREEN",
            duration_ms=12.5,
        )
        assert entry_id > 0

    def test_multiple_entries_increment_id(self, audit_db):
        """Verify sequential entries get incrementing IDs."""
        id1 = append_audit_entry(audit_db, event_type="first")
        id2 = append_audit_entry(audit_db, event_type="second")
        assert id2 > id1


class TestVerifyChain:
    """Tests for hash chain verification."""

    def test_empty_chain_is_valid(self, audit_db):
        """Verify empty audit log passes chain verification."""
        assert verify_chain(audit_db) is True

    def test_single_entry_chain_valid(self, audit_db):
        """Verify single entry chain passes verification."""
        append_audit_entry(audit_db, event_type="test")
        assert verify_chain(audit_db) is True

    def test_multi_entry_chain_valid(self, audit_db):
        """Verify chain with multiple entries passes."""
        for i in range(5):
            append_audit_entry(audit_db, event_type=f"event_{i}")
        assert verify_chain(audit_db) is True

    def test_tampered_entry_detected(self, audit_db):
        """Verify tampering with an entry is detected."""
        append_audit_entry(audit_db, event_type="original")
        audit_db.execute("UPDATE audit_log SET event_type = 'tampered' WHERE id = 1")
        audit_db.commit()
        assert verify_chain(audit_db) is False

    def test_tampered_hash_detected(self, audit_db):
        """Verify tampering with a hash is detected."""
        append_audit_entry(audit_db, event_type="test")
        append_audit_entry(audit_db, event_type="test2")
        audit_db.execute("UPDATE audit_log SET previous_hash = 'fake' WHERE id = 2")
        audit_db.commit()
        assert verify_chain(audit_db) is False


class TestAnchorLogging:
    """Tests for external hash anchor logging."""

    def test_anchor_logged_at_interval(self, audit_db, caplog):
        """Verify anchor is logged every 100 entries."""
        import logging

        with caplog.at_level(logging.INFO):
            for _ in range(100):
                append_audit_entry(audit_db, event_type="fill")
        assert "AUDIT ANCHOR" in caplog.text
