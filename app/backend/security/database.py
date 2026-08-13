"""SQLCipher database manager for encrypted data storage.

Manages the encrypted SQLCipher database connection lifecycle, including
opening with a 256-bit raw key, sentinel query validation, schema
initialization, and WAL journal mode configuration. Provides the single
database connection used by all Core process components.
"""

import logging
import re

import sqlcipher3


logger = logging.getLogger(__name__)

# --- Sentinel validation ---
SENTINEL_QUERY = "SELECT count(*) FROM sqlite_master"
RAW_KEY_BYTES = 32
RAW_KEY_HEX_CHARS = RAW_KEY_BYTES * 2
RAW_KEY_HEX_RE = re.compile(r"^[0-9a-f]+$")


class DatabaseError(Exception):
    """Base error for database operations."""


class DatabaseKeyError(DatabaseError):
    """Raised when the master key cannot decrypt the database."""


class DatabaseManager:
    """Manages a SQLCipher-encrypted database connection.

    Attributes:
        db_path: Path to the SQLCipher database file.
        connection: Active database connection or None if not connected.
    """

    def __init__(self, db_path: str) -> None:
        """Initialize the database manager.

        Args:
            db_path: Filesystem path to the SQLCipher database file.
        """
        self.db_path = db_path
        self.connection: sqlcipher3.Connection | None = None

    def open(self, master_key: bytearray) -> None:
        """Open the database with the given master key.

        Configures SQLCipher with the raw key, enables WAL journal mode,
        and validates the key by running a sentinel query.

        Args:
            master_key: 256-bit (32-byte) raw encryption key.

        Raises:
            DatabaseKeyError: If the key cannot decrypt the database.
            DatabaseError: If the database cannot be opened.
        """
        hex_key = self._raw_key_hex(master_key)
        try:
            conn = self._open_connection(hex_key)
            self._validate_sentinel(conn)
            self.connection = conn
            logger.info("Database opened successfully at %s", self.db_path)
        except DatabaseKeyError:
            raise
        except Exception as e:
            if self._looks_like_key_error(e):
                raise DatabaseKeyError("Master key cannot decrypt the database") from e
            raise DatabaseError(f"Failed to open database: {e}") from e

    @staticmethod
    def _raw_key_hex(master_key: bytes | bytearray) -> str:
        """Return validated SQLCipher raw key hex."""
        if not isinstance(master_key, (bytes, bytearray)):
            raise DatabaseError("Master key must be bytes-like")
        if len(master_key) != RAW_KEY_BYTES:
            raise DatabaseError("Master key must be 32 bytes")
        hex_key = master_key.hex()
        if (
            len(hex_key) != RAW_KEY_HEX_CHARS
            or RAW_KEY_HEX_RE.fullmatch(hex_key) is None
        ):
            raise DatabaseError("Master key hex shape is invalid")
        return hex_key

    def _open_connection(self, hex_key: str) -> sqlcipher3.Connection:
        """Open one SQLCipher connection and apply required pragmas."""
        connection = sqlcipher3.connect(self.db_path)
        connection.execute(f"PRAGMA key = \"x'{hex_key}'\"")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _looks_like_key_error(error: Exception) -> bool:
        """Return whether one database open failure looks like a wrong-key error."""
        error_msg = str(error).lower()
        return "not a database" in error_msg or "encrypted" in error_msg

    def _require_connection(self) -> sqlcipher3.Connection:
        """Return the active database connection or fail when the DB is closed."""
        if self.connection is None:
            raise self._closed_database_error()
        return self.connection

    @staticmethod
    def _closed_database_error() -> DatabaseError:
        """Build the standard error for database use before open()."""
        return DatabaseError("Database is not open")

    def _validate_sentinel(self, conn: sqlcipher3.Connection) -> None:
        """Run sentinel query to verify database key is correct.

        Args:
            conn: Database connection to validate.

        Raises:
            DatabaseKeyError: If the sentinel query fails.
        """
        try:
            conn.execute(SENTINEL_QUERY)
        except Exception as e:
            raise DatabaseKeyError(
                "Sentinel query failed — key does not decrypt this database"
            ) from e

    def close(self) -> None:
        """Close the database connection."""
        if self.connection is not None:
            self._require_connection().close()
            self.connection = None
            logger.info("Database connection closed")

    def execute(self, sql: str, params: tuple = ()) -> sqlcipher3.Cursor:
        """Execute a SQL statement on the database.

        Args:
            sql: SQL statement to execute.
            params: Parameters for the SQL statement.

        Returns:
            Cursor with query results.

        Raises:
            DatabaseError: If no connection is open.
        """
        return self._require_connection().execute(sql, params)

    def executemany(self, sql: str, params_seq: list[tuple]) -> sqlcipher3.Cursor:
        """Execute a SQL statement against multiple parameter sets.

        Args:
            sql: SQL statement to execute.
            params_seq: Sequence of parameter tuples.

        Returns:
            Cursor with results.

        Raises:
            DatabaseError: If no connection is open.
        """
        return self._require_connection().executemany(sql, params_seq)

    def commit(self) -> None:
        """Commit the current transaction.

        Raises:
            DatabaseError: If no connection is open.
        """
        self._require_connection().commit()

    def rollback(self) -> None:
        """Roll back the current transaction, if any."""
        self._require_connection().rollback()

    @property
    def in_transaction(self) -> bool:
        """Return whether the live connection has an open transaction."""
        return bool(self._require_connection().in_transaction)

    @property
    def is_open(self) -> bool:
        """Check if the database connection is open.

        Returns:
            True if connected, False otherwise.
        """
        return self.connection is not None
