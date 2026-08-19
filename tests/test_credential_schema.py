"""Tests for credential store schema initialization.

Covers table creation for integrations and credentials with
foreign key cascade behavior.
"""

import secrets

import pytest

from backend.security.credential_schema import initialize_credential_schema
from backend.security.database import DatabaseManager


@pytest.fixture
def cred_db(tmp_path):
    """Create a database with credential schema."""
    db = DatabaseManager(str(tmp_path / "cred.db"))
    db.open(bytearray(secrets.token_bytes(32)))
    initialize_credential_schema(db)
    yield db
    db.close()


class TestCredentialSchema:
    """Tests for credential store tables."""

    def test_integrations_table_created(self, cred_db):
        """Verify integrations table exists."""
        cursor = cred_db.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='table' AND name='integrations'"
        )
        assert cursor.fetchone()[0] == 1

    def test_credentials_table_created(self, cred_db):
        """Verify credentials table exists."""
        cursor = cred_db.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='table' AND name='credentials'"
        )
        assert cursor.fetchone()[0] == 1

    def test_insert_integration(self, cred_db):
        """Verify inserting an integration record."""
        cred_db.execute(
            "INSERT INTO integrations (id, name, service_type) VALUES (?, ?, ?)",
            ("int-1", "Email", "smtp"),
        )
        cred_db.commit()
        cursor = cred_db.execute(
            "SELECT name FROM integrations WHERE id = ?",
            ("int-1",),
        )
        assert cursor.fetchone()[0] == "Email"

    def test_cascade_delete_credentials(self, cred_db):
        """Verify credentials deleted with integration."""
        cred_db.execute(
            "INSERT INTO integrations (id, name, service_type) VALUES (?, ?, ?)",
            ("int-2", "API", "rest"),
        )
        cred_db.execute(
            "INSERT INTO credentials "
            "(id, integration_id, credential_type, value) "
            "VALUES (?, ?, ?, ?)",
            ("cred-1", "int-2", "api_key", "secret-value"),
        )
        cred_db.commit()
        cred_db.execute("DELETE FROM integrations WHERE id = ?", ("int-2",))
        cred_db.commit()
        cursor = cred_db.execute(
            "SELECT count(*) FROM credentials WHERE integration_id = ?",
            ("int-2",),
        )
        assert cursor.fetchone()[0] == 0
