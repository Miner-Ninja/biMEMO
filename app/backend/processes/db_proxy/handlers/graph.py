"""Handler factories for graph memory storage operations.

Provides make_save_entity_handler, make_save_assertion_handler,
make_invalidate_assertion_handler, make_get_entity_handler,
make_find_entities_handler, make_list_assertions_for_entity_handler,
make_find_graph_entry_points_handler, and
make_migrate_summaries_to_assertions_handler.

Entities are graph nodes; assertions are typed edges with a validity
window and evidence references. Writes are exposed on Socket A only;
reads on Sockets A and B (see registry allow-lists).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.memory.retrieval.fulltext import (
    index_assertion,
    index_entity,
    sanitize_fts_query,
)

from ..common import (
    OperationHandler,
    validate_params,
)
from ..params import (
    FetchUnextractedRecordsParams,
    FindEntitiesParams,
    FindGraphEntryPointsParams,
    GetEntityParams,
    InvalidateAssertionParams,
    ListAssertionsForEntityParams,
    MigrateSummariesToAssertionsParams,
    SaveAssertionParams,
    SaveEntityParams,
)


if TYPE_CHECKING:
    from pydantic import BaseModel

    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

# The migration parks legacy summaries under one placeholder entity until
# the stage-2 extractor re-attributes them to real entities.
MIGRATION_ENTITY_TYPE = "unresolved"
MIGRATION_ENTITY_NAME = "legacy_summaries"
MIGRATION_RELATION = "states"


def _validate(
    params: dict[str, Any] | None,
    model_class: type[BaseModel],
) -> BaseModel:
    """Validate operation parameters against a Pydantic model."""
    return validate_params(params, model_class)


def _normalized_entity_name(raw_name: str) -> str:
    """Return the canonical form of one entity name (casefold + trim)."""
    return " ".join(raw_name.split()).casefold()


def _now_timestamp() -> str:
    """Return the current UTC timestamp in the schema's format."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _entity_row_dict(row: tuple) -> dict[str, Any]:
    """Convert one entities row into a response dict."""
    return {
        "id": row[0],
        "entity_type": row[1],
        "canonical_name": row[2],
        "aliases": json.loads(row[3]),
        "created_at": row[4],
    }


_ENTITY_FIELDS_SQL = (
    "SELECT id, entity_type, canonical_name, aliases, created_at FROM entities "
)

_ASSERTION_FIELDS_SQL = (
    "SELECT id, subject_entity_id, relation, object_entity_id, object_value, "
    "assertion_text, trust_level, assertion_scope, valid_from, invalid_from, "
    "invalidated_by_assertion_id, source_record_ids, created_at "
    "FROM assertions "
)


def _assertion_row_dict(row: tuple) -> dict[str, Any]:
    """Convert one assertions row into a response dict."""
    return {
        "id": row[0],
        "subject_entity_id": row[1],
        "relation": row[2],
        "object_entity_id": row[3],
        "object_value": row[4],
        "assertion_text": row[5],
        "trust_level": row[6],
        "assertion_scope": row[7],
        "valid_from": row[8],
        "invalid_from": row[9],
        "invalidated_by_assertion_id": row[10],
        "source_record_ids": json.loads(row[11]),
        "created_at": row[12],
    }


def _entity_exists(db: DatabaseManager, entity_id: str) -> bool:
    """Return whether one entity row exists."""
    row = db.execute(
        "SELECT 1 FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    return row is not None


def _get_or_create_entity(
    db: DatabaseManager,
    *,
    entity_type: str,
    raw_name: str,
    aliases: list[str],
) -> tuple[str, bool]:
    """Return (entity_id, created) for one type+name pair.

    The canonical name is normalized; the raw spelling joins the aliases
    when it differs from the canonical form.
    """
    canonical_name = _normalized_entity_name(raw_name)
    existing = db.execute(
        "SELECT id, aliases FROM entities "
        "WHERE entity_type = ? AND canonical_name = ?",
        (entity_type, canonical_name),
    ).fetchone()
    alias_set = {alias for alias in aliases if alias}
    if raw_name.strip() and raw_name.strip() != canonical_name:
        alias_set.add(raw_name.strip())
    if existing is not None:
        entity_id = existing[0]
        stored_aliases = set(json.loads(existing[1]))
        merged = stored_aliases | alias_set
        if merged != stored_aliases:
            db.execute(
                "UPDATE entities SET aliases = ? WHERE id = ?",
                (json.dumps(sorted(merged)), entity_id),
            )
            db.commit()
            row = db.execute(
                "SELECT rowid FROM entities WHERE id = ?",
                (entity_id,),
            ).fetchone()
            index_entity(
                db,
                row[0],
                canonical_name,
                json.dumps(sorted(merged)),
            )
            db.commit()
        return entity_id, False
    entity_id = str(uuid.uuid4())
    aliases_json = json.dumps(sorted(alias_set))
    cursor = db.execute(
        "INSERT INTO entities (id, entity_type, canonical_name, aliases) "
        "VALUES (?, ?, ?, ?)",
        (entity_id, entity_type, canonical_name, aliases_json),
    )
    db.commit()
    index_entity(db, cursor.lastrowid, canonical_name, aliases_json)
    db.commit()
    return entity_id, True


def make_save_entity_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_entity operation (get-or-create)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Get or create one entity by type and canonical name."""
        p = _validate(params, SaveEntityParams)
        if not p.canonical_name.strip():
            raise ValueError("canonical_name must not be empty")
        if not p.entity_type.strip():
            raise ValueError("entity_type must not be empty")
        entity_id, created = _get_or_create_entity(
            db,
            entity_type=p.entity_type.strip(),
            raw_name=p.canonical_name,
            aliases=p.aliases,
        )
        return {"entity_id": entity_id, "created": created}

    return handle


def make_save_assertion_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_assertion operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store one assertion edge after validating its entity references."""
        p = _validate(params, SaveAssertionParams)
        if not p.relation.strip():
            raise ValueError("relation must not be empty")
        if not p.assertion_text.strip():
            raise ValueError("assertion_text must not be empty")
        if p.object_entity_id is not None and p.object_value is not None:
            raise ValueError(
                "object_entity_id and object_value are mutually exclusive"
            )
        if not _entity_exists(db, p.subject_entity_id):
            raise ValueError(
                f"subject entity does not exist: {p.subject_entity_id}"
            )
        if p.object_entity_id is not None and not _entity_exists(
            db, p.object_entity_id
        ):
            raise ValueError(
                f"object entity does not exist: {p.object_entity_id}"
            )
        assertion_id = str(uuid.uuid4())
        cursor = db.execute(
            "INSERT INTO assertions "
            "(id, subject_entity_id, relation, object_entity_id, "
            "object_value, assertion_text, trust_level, assertion_scope, "
            "valid_from, source_record_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assertion_id,
                p.subject_entity_id,
                p.relation.strip(),
                p.object_entity_id,
                p.object_value,
                p.assertion_text,
                p.trust_level,
                p.assertion_scope,
                p.valid_from,
                json.dumps(p.source_record_ids),
            ),
        )
        db.commit()
        index_assertion(db, cursor.lastrowid, p.assertion_text)
        db.commit()
        return {"assertion_id": assertion_id}

    return handle


def make_invalidate_assertion_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for invalidate_assertion operation.

    Sets invalid_from and records the superseding assertion; the row is
    kept — fact history is never silently deleted.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Mark one assertion as no longer valid."""
        p = _validate(params, InvalidateAssertionParams)
        cursor = db.execute(
            "UPDATE assertions SET invalid_from = ?, "
            "invalidated_by_assertion_id = ? "
            "WHERE id = ? AND invalid_from IS NULL",
            (
                _now_timestamp(),
                p.invalidated_by_assertion_id,
                p.assertion_id,
            ),
        )
        db.commit()
        return {"invalidated": cursor.rowcount > 0}

    return handle


def make_get_entity_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_entity operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one entity by id."""
        p = _validate(params, GetEntityParams)
        row = db.execute(
            _ENTITY_FIELDS_SQL + "WHERE id = ?",
            (p.entity_id,),
        ).fetchone()
        return {"entity": _entity_row_dict(row) if row else None}

    return handle


def make_find_entities_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for find_entities operation (FTS over names)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Find entities matching a full-text query."""
        p = _validate(params, FindEntitiesParams)
        safe_query = sanitize_fts_query(p.query)
        if not safe_query:
            return {"entities": []}
        rows = db.execute(
            "SELECT e.id, e.entity_type, e.canonical_name, e.aliases, "
            "e.created_at "
            "FROM entities_fts JOIN entities e "
            "ON e.rowid = entities_fts.rowid "
            "WHERE entities_fts MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, p.limit),
        ).fetchall()
        return {"entities": [_entity_row_dict(row) for row in rows]}

    return handle


def make_list_assertions_for_entity_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_assertions_for_entity operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List assertions where the entity is subject or object."""
        p = _validate(params, ListAssertionsForEntityParams)
        validity_filter = "AND invalid_from IS NULL " if p.valid_only else ""
        rows = db.execute(
            _ASSERTION_FIELDS_SQL
            + "WHERE (subject_entity_id = ? OR object_entity_id = ?) "
            + validity_filter
            + "ORDER BY created_at DESC LIMIT ?",
            (p.entity_id, p.entity_id, p.limit),
        ).fetchall()
        assertions = []
        for row in rows:
            entry = _assertion_row_dict(row)
            entry["direction"] = (
                "subject" if entry["subject_entity_id"] == p.entity_id else "object"
            )
            assertions.append(entry)
        return {"assertions": assertions}

    return handle


def make_find_graph_entry_points_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for find_graph_entry_points operation.

    Returns matching entities and assertions in one list, each hit labeled
    with its kind — the traversal entry surface for stage-3 recall.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Find graph entry points matching a full-text query."""
        p = _validate(params, FindGraphEntryPointsParams)
        safe_query = sanitize_fts_query(p.query)
        if not safe_query:
            return {"entry_points": []}
        entry_points: list[dict[str, Any]] = []
        entity_rows = db.execute(
            "SELECT e.id, e.entity_type, e.canonical_name, e.aliases, "
            "e.created_at "
            "FROM entities_fts JOIN entities e "
            "ON e.rowid = entities_fts.rowid "
            "WHERE entities_fts MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, p.limit),
        ).fetchall()
        for row in entity_rows:
            hit = _entity_row_dict(row)
            hit["kind"] = "entity"
            entry_points.append(hit)
        assertion_rows = db.execute(
            "SELECT a.id, a.subject_entity_id, a.relation, "
            "a.object_entity_id, a.object_value, a.assertion_text, "
            "a.trust_level, a.assertion_scope, a.valid_from, a.invalid_from, "
            "a.invalidated_by_assertion_id, a.source_record_ids, a.created_at "
            "FROM assertions_fts JOIN assertions a "
            "ON a.rowid = assertions_fts.rowid "
            "WHERE assertions_fts MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, p.limit),
        ).fetchall()
        for row in assertion_rows:
            hit = _assertion_row_dict(row)
            hit["kind"] = "assertion"
            entry_points.append(hit)
        return {"entry_points": entry_points}

    return handle


def make_fetch_unextracted_records_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for fetch_unextracted_records operation.

    Returns untainted records whose ids are not cited as evidence by any
    assertion — the graph extractor's eligibility set.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Fetch untainted records not yet extracted into the graph."""
        p = _validate(params, FetchUnextractedRecordsParams)
        cited: set[str] = set()
        for (source_ids_json,) in db.execute(
            "SELECT source_record_ids FROM assertions",
        ).fetchall():
            try:
                cited.update(json.loads(source_ids_json))
            except (json.JSONDecodeError, TypeError):
                continue
        rows = db.execute(
            "SELECT id, summary, key_facts, action_items, "
            "source_message_ids, created_at FROM records "
            "WHERE tainted = 0 ORDER BY created_at ASC",
        ).fetchall()
        records = [
            {
                "id": r[0],
                "summary": r[1],
                "key_facts": json.loads(r[2]),
                "action_items": json.loads(r[3]),
                "source_message_ids": json.loads(r[4]),
                "created_at": r[5],
            }
            for r in rows
            if r[0] not in cited
        ][: p.limit]
        return {"records": records}

    return handle


def make_migrate_summaries_to_assertions_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for migrate_summaries_to_assertions operation.

    Idempotent: each summary id lands in migrated_from_summary_id (UNIQUE),
    so re-running creates no duplicates. Summary rows are read-only here.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Convert legacy summaries into placeholder-entity assertions."""
        _validate(params, MigrateSummariesToAssertionsParams)
        placeholder_id, _created = _get_or_create_entity(
            db,
            entity_type=MIGRATION_ENTITY_TYPE,
            raw_name=MIGRATION_ENTITY_NAME,
            aliases=[],
        )
        summary_rows = db.execute(
            "SELECT id, main_point, trust_level, summary_scope, "
            "source_record_ids, created_at FROM summaries",
        ).fetchall()
        migrated_count = 0
        for summary_row in summary_rows:
            (
                summary_id,
                main_point,
                trust_level,
                summary_scope,
                source_record_ids,
                created_at,
            ) = summary_row
            already = db.execute(
                "SELECT 1 FROM assertions WHERE migrated_from_summary_id = ?",
                (summary_id,),
            ).fetchone()
            if already is not None:
                continue
            assertion_id = str(uuid.uuid4())
            cursor = db.execute(
                "INSERT INTO assertions "
                "(id, subject_entity_id, relation, object_value, "
                "assertion_text, trust_level, assertion_scope, valid_from, "
                "source_record_ids, migrated_from_summary_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assertion_id,
                    placeholder_id,
                    MIGRATION_RELATION,
                    None,
                    main_point,
                    trust_level,
                    summary_scope,
                    created_at,
                    source_record_ids,
                    summary_id,
                ),
            )
            index_assertion(db, cursor.lastrowid, main_point)
            migrated_count += 1
        db.commit()
        return {"migrated": migrated_count, "total_summaries": len(summary_rows)}

    return handle
