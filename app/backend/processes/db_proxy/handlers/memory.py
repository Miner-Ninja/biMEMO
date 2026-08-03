"""Handler factories for memory record, summary, and embedding operations.

Provides make_save_record_handler, make_mark_tainted_handler,
make_save_summary_handler, make_merge_summary_sources_handler,
make_store_embedding_handler, make_fetch_untainted_records_handler,
make_count_distillations_handler, make_list_summaries_handler,
make_init_vector_table_handler, make_fetch_unembedded_records_handler,
make_prune_stale_summaries_handler, and make_prune_unlinked_records_handler.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.memory.models import (
    get_trust_level_score,
    normalize_summary_scope,
    normalize_trust_level,
)
from backend.memory.retrieval.fulltext import (
    index_observation,
    index_reflection,
)

from ..common import (
    OperationHandler,
    table_exists,
    validate_params,
)
from ..params import (
    FetchUnembeddedRecordsParams,
    FetchUntaintedRecordsParams,
    InitVectorTableParams,
    ListSummariesParams,
    MarkTaintedParams,
    MergeSummarySourcesParams,
    PruneStaleSummariesParams,
    PruneUnlinkedRecordsParams,
    SaveRecordParams,
    SaveSummaryParams,
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
            "key_facts, action_items, memory_kinds, source_message_ids"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                p.conversation_id,
                p.session_id,
                p.summary,
                key_facts_json,
                json.dumps(p.action_items),
                memory_kinds_json,
                json.dumps(p.source_message_ids),
            ),
        )
        db.commit()
        index_observation(db, cursor.lastrowid, p.summary, key_facts_json)
        db.commit()
        return {"record_id": record_id}

    return handle


def make_mark_tainted_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for mark_tainted_record operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Mark an record as tainted."""
        p = _validate_params(params, MarkTaintedParams)
        db.execute(
            "UPDATE records SET tainted = 1 WHERE id = ?",
            (p.record_id,),
        )
        db.commit()
        return {"ok": True}

    return handle


def make_save_summary_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_summary operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Save a summary to the database."""
        p = _validate_params(params, SaveSummaryParams)
        summary_id = str(uuid.uuid4())
        normalized_trust_level = normalize_trust_level(p.trust_level)
        based_on_json = json.dumps(p.based_on)
        cursor = db.execute(
            "INSERT INTO summaries "
            "("
            "id, main_point, based_on, trust_level, summary_scope, "
            "trust_level_score, source_record_ids, record_relations"
            ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary_id,
                p.main_point,
                based_on_json,
                normalized_trust_level.value,
                normalize_summary_scope(p.summary_scope).value,
                get_trust_level_score(normalized_trust_level.value),
                json.dumps(p.source_record_ids),
                json.dumps([link.model_dump() for link in p.record_relations]),
            ),
        )
        index_reflection(db, cursor.lastrowid, p.main_point, based_on_json)
        db.commit()
        return {"summary_id": summary_id}

    return handle


def make_merge_summary_sources_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for merge_summary_sources operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Attach new source records to an existing summary."""
        p = _validate_params(params, MergeSummarySourcesParams)
        from backend.memory.summary_distiller.store import append_summary_sources

        updated = append_summary_sources(
            db,
            p.summary_id,
            p.source_record_ids,
            [link.model_dump() for link in p.record_relations],
            main_point=p.main_point,
            based_on=p.based_on,
            trust_level=p.trust_level,
            summary_scope=p.summary_scope,
            trust_level_score=p.trust_level_score,
        )
        return {"updated": updated}

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


def make_fetch_untainted_records_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for fetch_untainted_records operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Fetch untainted records for distiller."""
        p = _validate_params(params, FetchUntaintedRecordsParams)
        from backend.memory.storage.tainted import fetch_untainted_for_distiller

        rows = fetch_untainted_for_distiller(db, limit=p.limit)
        return {
            "records": [
                {
                    "id": r[0],
                    "summary": r[1],
                    "key_facts": r[2],
                    "action_items": r[3],
                    "memory_kinds": r[4],
                    "source_message_ids": r[5],
                }
                for r in rows
            ]
        }

    return handle


def make_count_distillations_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for count_distillations operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Count total summaries in the database."""
        from backend.memory.summary_distiller.store import count_distillations

        count = count_distillations(db)
        return {"count": count}

    return handle


def make_list_summaries_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_summaries operation.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List summaries ordered by trust level and creation date."""
        p = _validate_params(params, ListSummariesParams)
        from backend.memory.summary_distiller.store import (
            list_revalidated_distillations,
        )

        rows = list_revalidated_distillations(
            db,
            limit=p.limit,
            offset=p.offset,
        )
        return {
            "summaries": [
                {
                    "id": row["id"],
                    "main_point": row["main_point"],
                    "trust_level": row["trust_level"],
                    "scope": row["scope"],
                    "created_at": row["created_at"],
                    "based_on_count": len(row["based_on"]),
                }
                for row in rows
            ]
        }

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


def make_prune_stale_summaries_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for prune_stale_summaries operation.

    Deletes summaries whose derived revalidation status is in the
    requested statuses AND whose age exceeds age_days.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """
    from backend.memory.retrieval.fulltext import delete_summary_index

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete summaries a newer summary has already outdated.

        The revalidation status is read from the same read-path helpers the
        ranker and evidence chain use, so pruning cannot diverge from what
        ranking sees. The keep/delete decision is the pure
        ``should_prune_summary`` policy helper.
        """
        from backend.memory.storage.pruning import should_prune_summary
        from backend.memory.summary_distiller.store import (
            _build_revalidated_distillation_record,
            _load_distillation_rows,
            _parse_timestamp,
        )

        p = _validate_params(params, PruneStaleSummariesParams)
        now = datetime.now(UTC)

        deleted = 0
        for row in _load_distillation_rows(db, None):
            record = _build_revalidated_distillation_record(db, row)
            created_at = _parse_timestamp(str(record.get("created_at") or ""))
            if created_at is None:
                continue
            if not should_prune_summary(
                status=str(record["revalidation_status"]),
                age_days=(now - created_at).days,
                grace_days=p.grace_days,
            ):
                continue

            rowid_row = db.execute(
                "SELECT rowid FROM summaries WHERE id = ?",
                (record["id"],),
            ).fetchone()
            if rowid_row is None:
                continue
            db.execute("DELETE FROM summaries WHERE id = ?", (record["id"],))
            delete_summary_index(db, rowid_row[0])
            deleted += 1

        db.commit()
        return {"deleted": deleted}

    return handle


def make_prune_unlinked_records_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for prune_unlinked_records operation.

    Deletes records older than age_days that are not referenced by
    any summary (via source_record_ids or record_relations).

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """
    from backend.memory.retrieval.fulltext import delete_record_index

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete unlinked records by age."""
        from datetime import timedelta

        from backend.memory.summary_distiller.store import _parse_timestamp

        p = _validate_params(params, PruneUnlinkedRecordsParams)

        # Collect all record IDs referenced by any summary
        referenced_ids = set()
        summary_rows = db.execute(
            "SELECT source_record_ids, record_relations FROM summaries",
        ).fetchall()
        for src_ids, raw_relations in summary_rows:
            if src_ids:
                try:
                    ids = json.loads(src_ids)
                    if isinstance(ids, list):
                        referenced_ids.update(ids)
                except json.JSONDecodeError, TypeError:
                    pass
            if raw_relations:
                try:
                    rels = json.loads(raw_relations)
                    if isinstance(rels, list):
                        for rel in rels:
                            if isinstance(rel, dict) and rel.get("record_id"):
                                referenced_ids.add(str(rel["record_id"]))
                except json.JSONDecodeError, TypeError:
                    pass

        # Load all records
        rows = db.execute(
            "SELECT id, created_at, rowid FROM records",
        ).fetchall()

        cutoff = datetime.now(UTC) - timedelta(days=p.age_days)

        deleted = 0
        for record_id, created_at, rowid in rows:
            # Check age
            record_time = _parse_timestamp(created_at)
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


def make_list_conflicted_summaries_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_conflicted_summaries operation.

    Returns the id and main point of every summary whose derived
    revalidation status is ``conflicted``, using the same read-path
    derivation the ranker and evidence chain use. Read-only; it never
    mutates a summary.

    Args:
        db: DatabaseManager instance.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return conflicted summaries as {summary_id, main_point} pairs."""
        from backend.memory.summary_distiller.store import (
            _build_revalidated_distillation_record,
            _load_distillation_rows,
        )

        conflicted = [
            {
                "summary_id": str(record["id"]),
                "main_point": str(record.get("main_point") or ""),
            }
            for record in (
                _build_revalidated_distillation_record(db, row)
                for row in _load_distillation_rows(db, None)
            )
            if str(record["revalidation_status"]) == "conflicted"
        ]
        return {"summaries": conflicted}

    return handle
