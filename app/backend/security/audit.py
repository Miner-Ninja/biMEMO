"""Append-only audit log with SHA-256 hash chain integrity verification.

Records all security-relevant events in an append-only table with a
hash chain for tamper detection. Each entry's hash includes the previous
entry's hash, creating a verifiable chain. An external hash anchor is
written to stdout (Docker logs) every N entries.
"""

import hashlib
import json
import logging
import time

from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

# --- Audit log configuration ---
HASH_ANCHOR_INTERVAL = 100
INITIALIZATION_VECTOR = "bimemo-audit-chain-v1"

CREATE_AUDIT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
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
)
"""

CREATE_AUDIT_LOG_TIMESTAMP_IDX = """
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
ON audit_log(timestamp)
"""


def _initial_chain_hash() -> str:
    """Return the starting hash for an empty audit chain."""
    return hashlib.sha256(INITIALIZATION_VECTOR.encode()).hexdigest()


def _audit_entry_fields(
    previous_hash: str,
    timestamp: float,
    event_type: str,
    tool_name: str | None,
    parameters: str | None,
    result: str | None,
    zone: str | None,
    duration_ms: float | None,
) -> list[object]:
    """Return the ordered audit entry fields used for hashing."""
    return [
        previous_hash,
        timestamp,
        event_type,
        tool_name,
        parameters,
        result,
        zone,
        duration_ms,
    ]


def initialize_audit_schema(db: DatabaseManager) -> None:
    """Create audit log table and indexes.

    Args:
        db: An open DatabaseManager instance.
    """
    db.execute(CREATE_AUDIT_LOG_TABLE)
    db.execute(CREATE_AUDIT_LOG_TIMESTAMP_IDX)
    db.commit()


def _compute_entry_hash(
    previous_hash: str,
    timestamp: float,
    event_type: str,
    tool_name: str | None,
    parameters: str | None,
    result: str | None,
    zone: str | None,
    duration_ms: float | None,
) -> str:
    """Compute SHA-256 hash for an audit log entry.

    Args:
        previous_hash: Hash of the previous entry in the chain.
        timestamp: Unix timestamp of the event.
        event_type: Type of event being logged.
        tool_name: Name of the tool involved, if any.
        parameters: JSON-encoded parameters (credentials redacted).
        result: Result or outcome description.
        zone: Trust zone of the operation.
        duration_ms: Execution duration in milliseconds.

    Returns:
        Hex-encoded SHA-256 hash of the entry.
    """
    data = json.dumps(
        _audit_entry_fields(
            previous_hash,
            timestamp,
            event_type,
            tool_name,
            parameters,
            result,
            zone,
            duration_ms,
        ),
        sort_keys=True,
    )
    return hashlib.sha256(data.encode()).hexdigest()


def _get_last_hash(db: DatabaseManager) -> str:
    """Retrieve the hash of the most recent audit log entry.

    Args:
        db: An open DatabaseManager instance.

    Returns:
        The last entry hash or the initialization vector hash.
    """
    cursor = db.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        return _initial_chain_hash()
    return row[0]


def _should_log_hash_anchor(entry_id: int | None) -> bool:
    """Return whether one audit entry should emit a hash anchor log."""
    return entry_id is not None and entry_id % HASH_ANCHOR_INTERVAL == 0


def append_audit_entry(
    db: DatabaseManager,
    event_type: str,
    tool_name: str | None = None,
    parameters: str | None = None,
    result: str | None = None,
    zone: str | None = None,
    duration_ms: float | None = None,
) -> int:
    """Append a new entry to the audit log with hash chain.

    Args:
        db: An open DatabaseManager instance.
        event_type: Type of event being logged.
        tool_name: Name of the tool involved, if any.
        parameters: JSON-encoded parameters (credentials redacted).
        result: Result or outcome description.
        zone: Trust zone of the operation.
        duration_ms: Execution duration in milliseconds.

    Returns:
        ID of the newly created audit log entry.
    """
    timestamp = time.time()
    previous_hash = _get_last_hash(db)
    entry_hash = _compute_entry_hash(
        previous_hash,
        timestamp,
        event_type,
        tool_name,
        parameters,
        result,
        zone,
        duration_ms,
    )

    cursor = db.execute(
        "INSERT INTO audit_log "
        "(timestamp, event_type, tool_name, parameters, "
        "result, zone, duration_ms, "
        "previous_hash, entry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            timestamp,
            event_type,
            tool_name,
            parameters,
            result,
            zone,
            duration_ms,
            previous_hash,
            entry_hash,
        ),
    )
    db.commit()

    entry_id = cursor.lastrowid
    if _should_log_hash_anchor(entry_id):
        logger.info(
            "AUDIT ANCHOR: entry_id=%d hash=%s",
            entry_id,
            entry_hash,
        )

    return entry_id


def verify_chain(db: DatabaseManager) -> bool:
    """Verify the integrity of the entire audit log hash chain.

    Args:
        db: An open DatabaseManager instance.

    Returns:
        True if the chain is valid, False if tampering detected.
    """
    expected_prev = _initial_chain_hash()
    cursor = db.execute(
        "SELECT id, timestamp, event_type, tool_name, "
        "parameters, result, zone, duration_ms, "
        "previous_hash, entry_hash "
        "FROM audit_log ORDER BY id ASC"
    )

    for row in cursor:
        (
            entry_id,
            timestamp,
            event_type,
            tool_name,
            parameters,
            result,
            zone,
            duration_ms,
            previous_hash,
            entry_hash,
        ) = row

        if previous_hash != expected_prev:
            logger.warning(
                "Audit chain broken at entry %d",
                entry_id,
            )
            return False

        computed = _compute_entry_hash(
            previous_hash,
            timestamp,
            event_type,
            tool_name,
            parameters,
            result,
            zone,
            duration_ms,
        )
        if computed != entry_hash:
            logger.warning(
                "Audit chain tampered at entry %d",
                entry_id,
            )
            return False

        expected_prev = entry_hash

    return True
