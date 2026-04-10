"""Credential store schema for integrations and secrets.

Defines SQL table creation for storing integration configurations and
their associated credentials within the encrypted SQLCipher database.
No plain text credentials exist on disk — all are protected by the
database encryption.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

CREATE_INTEGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS integrations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    service_type TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
)
"""

CREATE_CREDENTIALS_TABLE = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    integration_id TEXT NOT NULL,
    credential_type TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    FOREIGN KEY (integration_id) REFERENCES integrations(id) ON DELETE CASCADE
)
"""

CREATE_CREDENTIALS_INTEGRATION_IDX = """
CREATE INDEX IF NOT EXISTS idx_credentials_integration_id
ON credentials(integration_id)
"""

CREDENTIAL_SCHEMA_STATEMENTS = [
    CREATE_INTEGRATIONS_TABLE,
    CREATE_CREDENTIALS_TABLE,
    CREATE_CREDENTIALS_INTEGRATION_IDX,
]


def _credential_schema_statements() -> list[str]:
    """Return the ordered credential schema statements."""
    return CREDENTIAL_SCHEMA_STATEMENTS


def _apply_schema_statements(
    db: DatabaseManager,
    statements: list[str],
) -> None:
    """Execute one ordered list of schema statements."""
    for statement in statements:
        db.execute(statement)


def initialize_credential_schema(
    db: DatabaseManager,
) -> None:
    """Create credential store tables if they do not exist.

    Args:
        db: An open DatabaseManager instance.
    """
    _apply_schema_statements(db, _credential_schema_statements())
    db.commit()
