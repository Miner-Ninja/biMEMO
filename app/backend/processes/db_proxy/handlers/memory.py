"""Handler factories for memory record and embedding operations.

Provides the record-queue handlers, make_save_record_handler,
make_store_embedding_handler, make_init_vector_table_handler,
make_fetch_unembedded_records_handler, and
make_prune_unlinked_records_handler.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.memory.retrieval.fulltext import (
    index_observation,
)

from ..common import (
    OperationHandler,
    table_exists,
    validate_params,
)
from ..params import (
    BumpQueuedRecordAttemptsParams,
    DeleteQueuedRecordsParams,
    FetchUnembeddedRecordsParams,
    InitVectorTableParams,
    ListQueuedRecordsParams,
    PruneUnlinkedRecordsParams,
    SaveQueuedRecordParams,
    SaveRecordParams,
    StoreEmbeddingParams,
)


if TYPE_CHECKING:
    from pydantic import BaseModel

    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)


def _validate_params(
    params: dict[str, Any] | None,
    model_class: type[BaseModel],
) -> BaseModel:
    """Validate operation parameters against a Pydantic model."""
    return validate_params(params, model_class)


def _table_exists(db: DatabaseManager, table_name: str) -> bool:
    """Return whether a table exists in the current SQLite schema."""
    return table_exists(db, table_name)


# Pending queue rows older than this are dropped by the self-pruning read:
# they survived multiple restarts without being processed and retrying them
# weeks later would only pollute memory with stale dialog steps.
QUEUED_RECORD_RETENTION_DAYS = 7


def _parse_record_timestamp(raw: object) -> datetime | None:
    """Parse one stored timestamp into an aware UTC datetime."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def make_save_queued_record_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_queued_record operation.

    Persists one pending dialog-recorder queue item so a crash or restart
    of the orchestrator does not lose un-recorded dialog steps.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist one pending dialog-recorder queue item."""
        p = _validate_params(params, SaveQueuedRecordParams)
        queue_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO record_queue "
            "(id, conversation_id, session_id, messages, "
            "source_message_ids, injection_flagged) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                queue_id,
                p.conversation_id,
                p.session_id,
                json.dumps(p.messages),
                json.dumps(p.source_message_ids),
                int(p.injection_flagged),
            ),
        )
        db.commit()
        return {"queue_id": queue_id}

    return handle


def make_delete_queued_records_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for delete_queued_records operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete processed queue rows by id."""
        p = _validate_params(params, DeleteQueuedRecordsParams)
        for queue_id in p.queue_ids:
            db.execute(
                "DELETE FROM record_queue WHERE id = ?",
                (queue_id,),
            )
        db.commit()
        return {"deleted": len(p.queue_ids)}

    return handle


def make_list_queued_records_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_queued_records operation.

    Self-prunes rows older than the retention window before reading, so
    perpetually unprocessed items cannot accumulate without bound.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List pending queue rows, oldest first."""
        p = _validate_params(params, ListQueuedRecordsParams)
        db.execute(
            "DELETE FROM record_queue WHERE created_at < "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now', ?)",
            (f"-{QUEUED_RECORD_RETENTION_DAYS} days",),
        )
        db.commit()
        rows = db.execute(
            "SELECT id, conversation_id, session_id, messages, "
            "source_message_ids, injection_flagged, attempts "
            "FROM record_queue ORDER BY created_at ASC LIMIT ?",
            (p.limit,),
        ).fetchall()
        return {
            "items": [
                {
                    "queue_id": r[0],
                    "conversation_id": r[1],
                    "session_id": r[2],
                    "messages": json.loads(r[3]),
                    "source_message_ids": json.loads(r[4]),
                    "injection_flagged": bool(r[5]),
                    "attempts": r[6],
                }
                for r in rows
            ]
        }

    return handle


def make_bump_queued_record_attempts_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for bump_queued_record_attempts operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Increment the attempt counter on pending queue rows."""
        p = _validate_params(params, BumpQueuedRecordAttemptsParams)
        for queue_id in p.queue_ids:
            db.execute(
                "UPDATE record_queue SET attempts = attempts + 1 WHERE id = ?",
                (queue_id,),
            )
        db.commit()
        return {"bumped": len(p.queue_ids)}

    return handle


def make_save_record_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_record operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Save an record to the database."""
        p = _validate_params(params, SaveRecordParams)
        if p.conversation_id and _table_exists(db, "conversations"):
            row = db.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (p.conversation_id,),
            ).fetchone()
            if row is None:
                logger.info(
                    "Skipping save_record for deleted conversation_id=%s",
                    p.conversation_id,
                )
                return {
                    "record_id": "",
                    "skipped": True,
                    "reason": "conversation_deleted",
                }
        record_id = str(uuid.uuid4())
        key_facts_json = json.dumps(p.key_facts)
        memory_kinds_json = json.dumps(p.memory_kinds)
        cursor = db.execute(
            "INSERT INTO records "
            "("
            "id, conversation_id, session_id, summary, "
            "key_facts, action_items, memory_kinds, source_message_ids, "
            "tainted"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                p.conversation_id,
                p.session_id,
                p.summary,
                key_facts_json,
                json.dumps(p.action_items),
                memory_kinds_json,
                json.dumps(p.source_message_ids),
                int(p.tainted),
            ),
        )
        db.commit()
        # Tainted records stay out of the FTS index entirely: they are
        # excluded from distillation and must not surface via search either.
        if not p.tainted:
            index_observation(db, cursor.lastrowid, p.summary, key_facts_json)
            db.commit()
        return {"record_id": record_id}

    return handle


def make_store_embedding_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_embedding operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store an embedding vector for an record."""
        p = _validate_params(params, StoreEmbeddingParams)
        try:
            from backend.memory.retrieval.vector_store import store_embedding

            store_embedding(db, p.record_id, p.embedding)
            return {"ok": True}
        except Exception as e:
            logger.warning("store_embedding failed: %s", e)
            return {"ok": False, "error": str(e)}

    return handle


def make_init_vector_table_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for init_vector_table operation.

    Compares requested model/dimensions with stored metadata.
    If the model changed, drops and recreates the vector table
    (different models produce incompatible semantic spaces).

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Initialize or recreate the vector table based on model/dimensions."""
        from backend.memory.retrieval.vector_store import (
            create_vector_table,
            drop_vector_table,
            get_embedding_metadata,
            set_embedding_metadata,
        )

        p = _validate_params(params, InitVectorTableParams)
        stored_model, stored_dim = get_embedding_metadata(db)
        recreated = False

        need_recreate = False
        if stored_model is None:
            # No metadata — either first run or migration from legacy hardcoded table.
            # Drop any existing table to ensure correct dimensions.
            need_recreate = True
            logger.info(
                "No embedding metadata found — dropping legacy table if present",
            )
        elif stored_model != p.embedding_model or stored_dim != p.dimensions:
            need_recreate = True
            logger.info(
                (
                    "Embedding config changed "
                    "(model: %s→%s, dim: %s→%s) — recreating vector table"
                ),
                stored_model,
                p.embedding_model,
                stored_dim,
                p.dimensions,
            )

        if need_recreate:
            drop_vector_table(db)
            recreated = True

        create_vector_table(db, p.dimensions)

        # Validate that the table's actual dimensions match the expected ones.
        # CREATE IF NOT EXISTS is a no-op if the table already exists with
        # different dimensions (e.g. from a legacy hardcoded setup).
        if not recreated:
            from backend.memory.retrieval.vector_store import search_similar

            try:
                search_similar(db, [0.0] * p.dimensions, 1)
            except Exception as exc:
                if "mismatch" in str(exc).lower():
                    logger.info(
                        (
                            "Dimension mismatch detected in existing table "
                            "— forcing recreate"
                        ),
                    )
                    drop_vector_table(db)
                    create_vector_table(db, p.dimensions)
                    recreated = True
                else:
                    raise

        set_embedding_metadata(db, p.embedding_model, p.dimensions)
        return {"ok": True, "recreated": recreated}

    return handle


def make_fetch_unembedded_records_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for fetch_unembedded_records operation.

    Returns records that have no corresponding embedding entry,
    ordered by creation time ascending (oldest first).

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Fetch records missing embeddings."""
        p = _validate_params(params, FetchUnembeddedRecordsParams)
        if not _table_exists(db, "record_embeddings"):
            rows = db.execute(
                "SELECT id, summary FROM records "
                "WHERE tainted = 0 ORDER BY created_at ASC LIMIT ?",
                (p.limit,),
            ).fetchall()
            return {"records": [{"id": r[0], "summary": r[1]} for r in rows]}

        try:
            rows = db.execute(
                "SELECT o.id, o.summary FROM records o "
                "WHERE o.tainted = 0 AND o.id NOT IN ("
                "  SELECT record_id FROM record_embeddings"
                ") ORDER BY o.created_at ASC LIMIT ?",
                (p.limit,),
            ).fetchall()
        except Exception:
            # sqlite-vec virtual tables may not support subquery in all versions;
            # fall back to a two-step approach
            logger.debug(
                "Subquery on vec0 table failed, using two-step fallback",
                exc_info=True,
            )
            embedded_rows = db.execute(
                "SELECT record_id FROM record_embeddings"
            ).fetchall()
            embedded_ids = {r[0] for r in embedded_rows}
            all_rows = db.execute(
                "SELECT id, summary FROM records "
                "WHERE tainted = 0 ORDER BY created_at ASC",
            ).fetchall()
            rows = [r for r in all_rows if r[0] not in embedded_ids][: p.limit]

        return {"records": [{"id": r[0], "summary": r[1]} for r in rows]}

    return handle


def make_prune_unlinked_records_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for prune_unlinked_records operation.

    Deletes records older than age_days that no assertion cites as
    evidence (source_record_ids).

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """
    from backend.memory.retrieval.fulltext import delete_record_index

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete unlinked records by age."""
        from datetime import timedelta

        p = _validate_params(params, PruneUnlinkedRecordsParams)

        # A record is protected while any assertion cites it as evidence.
        referenced_ids = set()
        assertion_rows = db.execute(
            "SELECT source_record_ids FROM assertions",
        ).fetchall()
        for (src_ids,) in assertion_rows:
            if not src_ids:
                continue
            try:
                ids = json.loads(src_ids)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(ids, list):
                referenced_ids.update(str(record_id) for record_id in ids)

        # Load all records
        rows = db.execute(
            "SELECT id, created_at, rowid FROM records",
        ).fetchall()

        cutoff = datetime.now(UTC) - timedelta(days=p.age_days)

        deleted = 0
        for record_id, created_at, rowid in rows:
            # Check age
            record_time = _parse_record_timestamp(created_at)
            if record_time is None or record_time >= cutoff:
                continue

            # Check if referenced
            if record_id in referenced_ids:
                continue

            # Delete this record
            db.execute(
                "DELETE FROM records WHERE id = ?",
                (record_id,),
            )
            # Delete FTS index
            delete_record_index(db, rowid)
            # Delete embedding if exists
            with contextlib.suppress(Exception):
                db.execute(
                    "DELETE FROM record_embeddings WHERE record_id = ?",
                    (record_id,),
                )

            deleted += 1

        db.commit()
        return {"deleted": deleted}

    return handle


