"""Vector storage and search using sqlite-vec extension.

Provides embedding storage and cosine similarity search for records
within the SQLCipher database. Uses sqlite-vec extension for efficient
vector operations on locally generated embeddings.
"""

import json
import logging
from typing import TYPE_CHECKING

import sqlite_vec


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

# --- Vector configuration ---
VECTOR_TABLE_NAME = "record_embeddings"
EMBEDDING_MODEL_METADATA_KEY = "embedding_model"
EMBEDDING_DIMENSIONS_METADATA_KEY = "embedding_dimensions"
EMBEDDING_METRIC_METADATA_KEY = "embedding_distance_metric"
# Embeddings compare by angle, not magnitude; without an explicit metric
# vec0 defaults to L2. The stored metadata value lets init_vector_table
# rebuild tables created before the metric was pinned.
VECTOR_DISTANCE_METRIC = "cosine"

# --- SQL templates ---
INSERT_SQL = "INSERT INTO record_embeddings (record_id, embedding) VALUES (?, ?)"
SEARCH_SQL = (
    "SELECT record_id, distance FROM record_embeddings "
    "WHERE embedding MATCH ? ORDER BY distance LIMIT ?"
)


def _require_open_connection(db: DatabaseManager) -> sqlite_vec.Connection:
    """Return the open sqlite connection or raise a clear error."""
    if db.connection is None:
        msg = "Database must be open before loading vector extension"
        raise RuntimeError(msg)
    return db.connection


def _metadata_value(db: DatabaseManager, key: str) -> bytes | None:
    """Read one raw metadata value."""
    row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row is not None else None


def _set_metadata_value(db: DatabaseManager, key: str, value: bytes) -> None:
    """Store one raw metadata value."""
    db.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        (key, value),
    )


def _serialized_embedding(embedding: list[float]) -> str:
    """Serialize one embedding vector for sqlite-vec queries and inserts."""
    return json.dumps(embedding)


def enable_vector_extension(db: DatabaseManager) -> None:
    """Load the sqlite-vec extension into the database connection.

    Args:
        db: An open DatabaseManager instance.
    """
    connection = _require_open_connection(db)
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    logger.info("sqlite-vec extension loaded")


def create_vector_table(db: DatabaseManager, dimensions: int) -> None:
    """Create the virtual table for storing record embeddings.

    Args:
        db: An open DatabaseManager instance.
        dimensions: Number of dimensions for embedding vectors.
    """
    sql = (
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {VECTOR_TABLE_NAME} "
        f"USING vec0("
        f"record_id TEXT PRIMARY KEY, "
        f"embedding float[{dimensions}] "
        f"distance_metric={VECTOR_DISTANCE_METRIC})"
    )
    db.execute(sql)
    db.commit()


def drop_vector_table(db: DatabaseManager) -> None:
    """Drop the record embeddings virtual table.

    Args:
        db: An open DatabaseManager instance.
    """
    db.execute(f"DROP TABLE IF EXISTS {VECTOR_TABLE_NAME}")
    db.commit()
    logger.info("Dropped vector table %s", VECTOR_TABLE_NAME)


def get_embedding_metadata(db: DatabaseManager) -> tuple[str | None, int | None]:
    """Read the stored embedding model name and dimensions from metadata.

    Args:
        db: An open DatabaseManager instance.

    Returns:
        Tuple of (model_name, dimensions). Both None if not yet stored.
    """
    model_value = _metadata_value(db, EMBEDDING_MODEL_METADATA_KEY)
    model = model_value.decode() if model_value is not None else None

    dimensions_value = _metadata_value(db, EMBEDDING_DIMENSIONS_METADATA_KEY)
    dimensions = (
        int(dimensions_value.decode()) if dimensions_value is not None else None
    )

    return model, dimensions


def get_embedding_metric(db: DatabaseManager) -> str | None:
    """Read the stored vector distance metric from metadata.

    Returns:
        The metric name, or None for tables created before the metric
        was pinned (those were built with the vec0 default, L2).
    """
    metric_value = _metadata_value(db, EMBEDDING_METRIC_METADATA_KEY)
    return metric_value.decode() if metric_value is not None else None


def set_embedding_metadata(db: DatabaseManager, model: str, dimensions: int) -> None:
    """Store the embedding model name and dimensions in metadata.

    Args:
        db: An open DatabaseManager instance.
        model: Embedding model name.
        dimensions: Vector dimensionality.
    """
    _set_metadata_value(db, EMBEDDING_MODEL_METADATA_KEY, model.encode())
    _set_metadata_value(
        db,
        EMBEDDING_DIMENSIONS_METADATA_KEY,
        str(dimensions).encode(),
    )
    _set_metadata_value(
        db,
        EMBEDDING_METRIC_METADATA_KEY,
        VECTOR_DISTANCE_METRIC.encode(),
    )
    db.commit()


def store_embedding(
    db: DatabaseManager,
    record_id: str,
    embedding: list[float],
) -> None:
    """Store an embedding vector for an record.

    Args:
        db: An open DatabaseManager instance.
        record_id: ID of the record this embedding belongs to.
        embedding: Float vector to store.
    """
    db.execute(INSERT_SQL, (record_id, _serialized_embedding(embedding)))
    db.commit()


def search_similar(
    db: DatabaseManager,
    query_embedding: list[float],
    limit: int,
) -> list[tuple[str, float]]:
    """Search for records with similar embeddings.

    Args:
        db: An open DatabaseManager instance.
        query_embedding: Query vector for similarity search.
        limit: Maximum number of results to return.

    Returns:
        List of (record_id, distance) tuples.
    """
    cursor = db.execute(SEARCH_SQL, (_serialized_embedding(query_embedding), limit))
    return cursor.fetchall()
