"""Optional data migration: clear leftover ``num_ctx = 4000`` overrides.

After the kv-cache-detection feature, the ``num_ctx`` field on integration
credentials is no longer a default-with-fallback but an **explicit UI
override**. Operators who previously set it to exactly ``4000`` as a
workaround for the silent fallback should typically clear it so that
auto-detect runs instead. This script removes ``num_ctx`` credential rows
whose value is exactly ``"4000"``; other values are left untouched
because they may be intentional limits.

Run inside the orchestrator container, against the live SQLCipher DB.
Requires the master key to already be set in the standard env vars.
"""

from __future__ import annotations

import logging
import sys


logger = logging.getLogger(__name__)

# --- Migration constants ---
LEFTOVER_NUM_CTX_VALUE = "4000"
TARGET_CREDENTIAL_TYPE = "num_ctx"
EXIT_OK = 0
EXIT_ERROR = 1


def _build_delete_statement() -> str:
    """Return the parameterized SQL that nulls out leftover 4000 overrides."""
    return (
        "DELETE FROM credentials "
        "WHERE credential_type = ? AND value = ?"
    )


def _run_migration(connection: object) -> int:
    """Execute the delete and return the number of affected rows.

    Args:
        connection: An open SQLCipher/sqlite3 connection with write access
            to the ``credentials`` table.

    Returns:
        Count of credential rows removed by the migration.
    """
    cursor = connection.execute(
        _build_delete_statement(),
        (TARGET_CREDENTIAL_TYPE, LEFTOVER_NUM_CTX_VALUE),
    )
    affected = cursor.rowcount
    connection.commit()
    return affected


def main() -> int:
    """CLI entry point: open the live DB, run the migration, log the result."""
    from backend.security.unlock import open_database

    logging.basicConfig(level=logging.INFO)
    try:
        with open_database() as connection:
            affected = _run_migration(connection)
    except Exception:
        logger.exception("Migration failed")
        return EXIT_ERROR
    logger.info(
        "Cleared %d leftover num_ctx=%s credential row(s)",
        affected,
        LEFTOVER_NUM_CTX_VALUE,
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
