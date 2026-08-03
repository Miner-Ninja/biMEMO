"""DB proxy handlers for encrypted route-diagnostics storage."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from backend.memory.summary_distiller.constants import (
    TRACE_DECISION_CONFLICT_PENDING,
)

from ..common import build_in_clause_query
from ..params import (
    ConflictedSummaryFlagParams,
    GetMemoryRouteDiagnosticsSummaryParams,
    ListMemoryRouteDiagnosticsParams,
    ListRecordProcessingDiagnosticsParams,
    ListSummaryProcessingDiagnosticsParams,
    WriteMemoryRouteDiagnosticParams,
    WriteRecordProcessingDiagnosticParams,
    WriteSummaryProcessingDiagnosticParams,
)


if TYPE_CHECKING:
    from backend.ipc.db_proxy import OperationHandler
    from backend.security.database import DatabaseManager


def _memory_route_diagnostic_row(
    payload: WriteMemoryRouteDiagnosticParams,
) -> tuple[Any, ...]:
    """Build the DB row tuple for one memory-route diagnostic insert."""
    return (
        payload.session_id,
        payload.conversation_id,
        payload.message_id,
        payload.channel,
        payload.initial_intent,
        payload.initial_confidence,
        payload.initial_role_filter,
        payload.initial_source,
        int(payload.fallback_attempted),
        payload.fallback_reason,
        payload.fallback_intent,
        payload.fallback_confidence,
        payload.fallback_role_filter,
        payload.fallback_source,
        payload.final_intent,
        payload.final_confidence,
        payload.final_role_filter,
        payload.final_source,
        int(payload.route_mismatch),
        payload.mismatch_label,
        int(payload.used_long_term_mode),
    )


def _memory_route_signal_rows(
    diagnostic_id: int,
    payload: WriteMemoryRouteDiagnosticParams,
) -> list[tuple[int, str, str]]:
    """Build signal-group rows for one memory-route diagnostic."""
    return [
        (diagnostic_id, "initial", signal_group)
        for signal_group in payload.initial_signal_groups
    ] + [
        (diagnostic_id, "final", signal_group)
        for signal_group in payload.final_signal_groups
    ]


def _record_processing_diagnostic_row(
    payload: WriteRecordProcessingDiagnosticParams,
) -> tuple[Any, ...]:
    """Build the DB row tuple for one record-processing diagnostic insert."""
    return (
        payload.decision,
        payload.conversation_id,
        payload.session_id,
        payload.record_id,
        payload.model,
        payload.message_count,
        json.dumps(payload.source_message_ids),
        json.dumps(payload.messages),
        int(payload.injection_flagged),
        payload.summary,
        json.dumps(payload.key_facts),
        json.dumps(payload.action_items),
        json.dumps(payload.memory_kinds),
        json.dumps(payload.prompt),
        payload.raw_output,
        payload.error,
        payload.detail,
    )


def _summary_processing_diagnostic_row(
    payload: WriteSummaryProcessingDiagnosticParams,
) -> tuple[Any, ...]:
    """Build the DB row tuple for one summary-processing diagnostic insert."""
    return (
        payload.decision,
        payload.summary_id,
        payload.model,
        payload.record_count,
        json.dumps(payload.source_record_ids),
        json.dumps(payload.records),
        json.dumps(payload.prompt),
        payload.raw_output,
        payload.main_point,
        json.dumps(payload.based_on),
        payload.trust_level,
        payload.scope,
        json.dumps(payload.record_relations),
        payload.original_scope,
        payload.scope_adjustment_reason,
        payload.error,
        payload.detail,
    )


def _memory_route_filter_clauses(
    payload: ListMemoryRouteDiagnosticsParams,
) -> tuple[list[str], list[Any]]:
    """Build WHERE clauses and parameters for route-diagnostic listing."""
    where_clauses: list[str] = []
    query_params: list[Any] = []
    if payload.final_intent:
        where_clauses.append("final_intent = ?")
        query_params.append(payload.final_intent)
    if payload.mismatch_only:
        where_clauses.append("route_mismatch = 1")
    if payload.fallback_only:
        where_clauses.append("fallback_attempted = 1")
    return where_clauses, query_params


def _memory_route_list_query(
    payload: ListMemoryRouteDiagnosticsParams,
) -> tuple[str, tuple[Any, ...]]:
    """Build the SQL query and parameters for route-diagnostic listing."""
    where_clauses, query_params = _memory_route_filter_clauses(payload)
    query_params.extend([payload.limit, payload.offset])
    query_sql = """
        SELECT
            id,
            created_at,
            session_id,
            conversation_id,
            message_id,
            channel,
            initial_intent,
            initial_confidence,
            initial_role_filter,
            initial_source,
            fallback_attempted,
            fallback_reason,
            fallback_intent,
            fallback_confidence,
            fallback_role_filter,
            fallback_source,
            final_intent,
            final_confidence,
            final_role_filter,
            final_source,
            route_mismatch,
            mismatch_label,
            used_long_term_mode
        FROM memory_route_diagnostics
    """
    if where_clauses:
        query_sql += "\nWHERE " + " AND ".join(where_clauses)
    query_sql += "\nORDER BY created_at DESC, id DESC\nLIMIT ? OFFSET ?"
    return query_sql, tuple(query_params)


def _load_signal_groups_by_diagnostic(
    diagnostics_db: DatabaseManager,
    diagnostic_ids: list[int],
) -> dict[int, dict[str, list[str]]]:
    """Load grouped signal matches for the listed route diagnostics."""
    signal_groups_by_diagnostic: dict[int, dict[str, list[str]]] = {
        diagnostic_id: {"initial": [], "final": []} for diagnostic_id in diagnostic_ids
    }
    if not diagnostic_ids:
        return signal_groups_by_diagnostic

    signal_query = build_in_clause_query(
        """
        SELECT diagnostic_id, stage, signal_group
        FROM memory_route_signal_diagnostics
        WHERE diagnostic_id IN
        """,
        len(diagnostic_ids),
    )
    signal_query += "\nORDER BY diagnostic_id ASC, stage ASC, signal_group ASC"
    signal_rows = diagnostics_db.execute(
        signal_query,
        tuple(diagnostic_ids),
    ).fetchall()
    for diagnostic_id, stage, signal_group in signal_rows:
        signal_groups_by_diagnostic[int(diagnostic_id)][str(stage)].append(
            str(signal_group)
        )
    return signal_groups_by_diagnostic


def _memory_route_diagnostic_entry(
    row: tuple[Any, ...],
    signal_groups: dict[str, list[str]],
) -> dict[str, Any]:
    """Build one route-diagnostic response entry from a DB row."""
    diagnostic_id = int(row[0])
    return {
        "id": diagnostic_id,
        "created_at": row[1],
        "session_id": row[2],
        "conversation_id": row[3],
        "message_id": row[4],
        "channel": row[5],
        "initial_route": {
            "intent": row[6],
            "confidence": row[7],
            "role_filter": row[8],
            "source": row[9],
            "signal_groups": signal_groups["initial"],
        },
        "fallback": {
            "attempted": bool(row[10]),
            "reason": row[11],
            "intent": row[12],
            "confidence": row[13],
            "role_filter": row[14],
            "source": row[15],
        },
        "final_route": {
            "intent": row[16],
            "confidence": row[17],
            "role_filter": row[18],
            "source": row[19],
            "signal_groups": signal_groups["final"],
        },
        "route_mismatch": bool(row[20]),
        "mismatch_label": row[21],
        "used_long_term_mode": bool(row[22]),
    }


def _diagnostic_json_list(value: str | None) -> list[Any]:
    """Decode one stored JSON list field from diagnostics storage."""
    parsed = _diagnostic_json_entry(value)
    return parsed if isinstance(parsed, list) else []


def _diagnostic_json_entry(value: str | None) -> object:
    """Decode one optional JSON diagnostic field."""
    return json.loads(value) if value else []


def _record_processing_query(
    payload: ListRecordProcessingDiagnosticsParams,
) -> tuple[str, tuple[Any, ...]]:
    """Build the SQL query for record-processing diagnostic listing."""
    query = """
        SELECT
            created_at,
            decision,
            conversation_id,
            session_id,
            source_message_ids,
            messages,
            message_count,
            prompt,
            raw_output,
            summary,
            key_facts,
            action_items,
            memory_kinds,
            record_id,
            model,
            error,
            detail
        FROM record_processing_diagnostics
    """
    query_params: list[Any] = []
    if payload.decision:
        query += "\nWHERE decision = ?"
        query_params.append(payload.decision)
    query += "\nORDER BY created_at DESC, id DESC\nLIMIT ?"
    query_params.append(payload.limit)
    return query, tuple(query_params)


def _record_processing_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one record-processing diagnostic response entry from a DB row."""
    return {
        "timestamp": row[0],
        "decision": row[1],
        "conversation_id": row[2],
        "session_id": row[3],
        "source_message_ids": _diagnostic_json_list(row[4]),
        "messages": _diagnostic_json_list(row[5]),
        "message_count": row[6],
        "prompt": _diagnostic_json_list(row[7]),
        "raw_output": row[8],
        "summary": row[9],
        "key_facts": _diagnostic_json_list(row[10]),
        "action_items": _diagnostic_json_list(row[11]),
        "memory_kinds": _diagnostic_json_list(row[12]),
        "record_id": row[13],
        "model": row[14],
        "error": row[15],
        "detail": row[16],
    }


def _summary_processing_query(
    payload: ListSummaryProcessingDiagnosticsParams,
) -> tuple[str, tuple[Any, ...]]:
    """Build the SQL query for summary-processing diagnostic listing."""
    query = """
        SELECT
            created_at,
            decision,
            model,
            record_count,
            source_record_ids,
            records,
            prompt,
            raw_output,
            main_point,
            based_on,
            trust_level,
            scope,
            record_relations,
            summary_id,
            error,
            detail
        FROM summary_processing_diagnostics
    """
    query_params: list[Any] = []
    if payload.decision:
        query += "\nWHERE decision = ?"
        query_params.append(payload.decision)
    query += "\nORDER BY created_at DESC, id DESC\nLIMIT ?"
    query_params.append(payload.limit)
    return query, tuple(query_params)


def _summary_processing_entry(row: tuple[Any, ...]) -> dict[str, Any]:
    """Build one summary-processing diagnostic response entry from a DB row."""
    return {
        "timestamp": row[0],
        "decision": row[1],
        "model": row[2],
        "record_count": row[3],
        "source_record_ids": _diagnostic_json_list(row[4]),
        "records": _diagnostic_json_list(row[5]),
        "prompt": _diagnostic_json_list(row[6]),
        "raw_output": row[7],
        "main_point": row[8],
        "based_on": _diagnostic_json_list(row[9]),
        "trust_level": row[10],
        "scope": row[11],
        "record_relations": _diagnostic_json_list(row[12]),
        "summary_id": row[13],
        "error": row[14],
        "detail": row[15],
    }


def _memory_route_summary_window_params(
    payload: GetMemoryRouteDiagnosticsSummaryParams,
) -> tuple[str, tuple[Any, ...]]:
    """Build the optional recent-hours WHERE clause inputs for summary queries."""
    if payload.recent_hours is None:
        return "", ()
    return (
        """
        WHERE created_at >= strftime(
            '%Y-%m-%dT%H:%M:%f',
            'now',
            ?
        )
        """,
        (f"-{payload.recent_hours} hours",),
    )


def _recent_hours_filter_sql(recent_hours: int | None) -> tuple[str, tuple[Any, ...]]:
    """Return the shared created-at filter for an optional recent-hours window."""
    if recent_hours is None:
        return "", ()
    return (
        "WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%f', 'now', ?)",
        (f"-{recent_hours} hours",),
    )


def _memory_route_summary_totals(
    diagnostics_db: DatabaseManager,
    recent_hours: int | None,
    query_params: tuple[Any, ...],
) -> tuple[Any, ...]:
    """Load top-level route-diagnostic summary counts."""
    where_clause, where_params = _recent_hours_filter_sql(recent_hours)
    query = """
        SELECT
            count(*) AS total_rows,
            COALESCE(sum(fallback_attempted), 0) AS fallback_rows,
            COALESCE(sum(route_mismatch), 0) AS mismatch_rows,
            COALESCE(sum(used_long_term_mode), 0) AS long_term_rows
        FROM memory_route_diagnostics
    """
    if where_clause:
        query += f"\n{where_clause}"
    return diagnostics_db.execute(
        query,
        where_params or query_params,
    ).fetchone()


def _memory_route_summary_count_rows(
    diagnostics_db: DatabaseManager,
    summary_kind: str,
    recent_hours: int | None,
    query_params: tuple[Any, ...],
) -> list[tuple[Any, ...]]:
    """Load grouped count rows for one summary dimension."""
    query_by_kind = {
        "mismatch_label": """
            SELECT mismatch_label, count(*)
            FROM memory_route_diagnostics
            GROUP BY mismatch_label
            ORDER BY count(*) DESC, mismatch_label ASC
        """,
        "final_intent": """
            SELECT final_intent, count(*)
            FROM memory_route_diagnostics
            GROUP BY final_intent
            ORDER BY count(*) DESC, final_intent ASC
        """,
    }
    recent_query_by_kind = {
        "mismatch_label": """
            SELECT mismatch_label, count(*)
            FROM memory_route_diagnostics
            WHERE created_at >= strftime(
                '%Y-%m-%dT%H:%M:%f',
                'now',
                ?
            )
            GROUP BY mismatch_label
            ORDER BY count(*) DESC, mismatch_label ASC
        """,
        "final_intent": """
            SELECT final_intent, count(*)
            FROM memory_route_diagnostics
            WHERE created_at >= strftime(
                '%Y-%m-%dT%H:%M:%f',
                'now',
                ?
            )
            GROUP BY final_intent
            ORDER BY count(*) DESC, final_intent ASC
        """,
    }
    query = (
        query_by_kind[summary_kind]
        if recent_hours is None
        else recent_query_by_kind[summary_kind]
    )
    execute_params: tuple[Any, ...] = () if recent_hours is None else query_params
    return diagnostics_db.execute(query, execute_params).fetchall()


def _memory_route_fallback_reason_rows(
    diagnostics_db: DatabaseManager,
    recent_hours: int | None,
    query_params: tuple[Any, ...],
) -> list[tuple[Any, ...]]:
    """Load grouped fallback reasons for route-diagnostic summary output."""
    where_clause, where_params = _recent_hours_filter_sql(recent_hours)
    query = """
        SELECT fallback_reason, count(*)
        FROM memory_route_diagnostics
        WHERE fallback_attempted = 1
    """
    if where_clause:
        query += f"\n  AND {where_clause.removeprefix('WHERE ')}"
    query += "\nGROUP BY fallback_reason\nORDER BY count(*) DESC, fallback_reason ASC"
    return diagnostics_db.execute(
        query,
        where_params or query_params,
    ).fetchall()


def _label_count_entries(
    rows: list[tuple[Any, ...]],
    key_name: str,
    default_value: str | None = None,
) -> list[dict[str, Any]]:
    """Build one summary label/count list from grouped DB rows."""
    entries: list[dict[str, Any]] = []
    for label, count in rows:
        value = str(label if label is not None else default_value)
        entries.append({key_name: value, "count": int(count)})
    return entries


def make_write_memory_route_diagnostic_handler(
    diagnostics_db: DatabaseManager,
    retention_days: int = 14,
) -> OperationHandler:
    """Create handler for writing one structured memory-route diagnostic.

    Args:
        diagnostics_db: Open diagnostics database manager.
        retention_days: Number of days to keep route diagnostics.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist one route-diagnostic record and its signal groups."""
        from backend.security.diagnostics_schema import (
            prune_memory_route_diagnostics,
        )

        payload = WriteMemoryRouteDiagnosticParams.model_validate(params or {})
        prune_memory_route_diagnostics(diagnostics_db, retention_days)
        cursor = diagnostics_db.execute(
            """
            INSERT INTO memory_route_diagnostics (
                session_id,
                conversation_id,
                message_id,
                channel,
                initial_intent,
                initial_confidence,
                initial_role_filter,
                initial_source,
                fallback_attempted,
                fallback_reason,
                fallback_intent,
                fallback_confidence,
                fallback_role_filter,
                fallback_source,
                final_intent,
                final_confidence,
                final_role_filter,
                final_source,
                route_mismatch,
                mismatch_label,
                used_long_term_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _memory_route_diagnostic_row(payload),
        )
        diagnostic_id = int(cursor.lastrowid)

        signal_rows = _memory_route_signal_rows(diagnostic_id, payload)
        if signal_rows:
            diagnostics_db.executemany(
                """
                INSERT INTO memory_route_signal_diagnostics (
                    diagnostic_id,
                    stage,
                    signal_group
                ) VALUES (?, ?, ?)
                """,
                signal_rows,
            )
        diagnostics_db.commit()
        return {"diagnostic_id": diagnostic_id}

    return handle


def make_write_record_processing_diagnostic_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for writing one structured record-processing record."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = WriteRecordProcessingDiagnosticParams.model_validate(params or {})
        cursor = diagnostics_db.execute(
            """
            INSERT INTO record_processing_diagnostics (
                decision,
                conversation_id,
                session_id,
                record_id,
                model,
                message_count,
                source_message_ids,
                messages,
                injection_flagged,
                summary,
                key_facts,
                action_items,
                memory_kinds,
                prompt,
                raw_output,
                error,
                detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _record_processing_diagnostic_row(payload),
        )
        diagnostics_db.commit()
        return {"diagnostic_id": int(cursor.lastrowid)}

    return handle


def make_write_summary_processing_diagnostic_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for writing one structured summary-processing record."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = WriteSummaryProcessingDiagnosticParams.model_validate(params or {})
        cursor = diagnostics_db.execute(
            """
            INSERT INTO summary_processing_diagnostics (
                decision,
                summary_id,
                model,
                record_count,
                source_record_ids,
                records,
                prompt,
                raw_output,
                main_point,
                based_on,
                trust_level,
                scope,
                record_relations,
                original_scope,
                scope_adjustment_reason,
                error,
                detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _summary_processing_diagnostic_row(payload),
        )
        diagnostics_db.commit()
        return {"diagnostic_id": int(cursor.lastrowid)}

    return handle


def make_conflicted_summary_flag_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for the conflicted_summary_flag operation.

    Records one pending-conflict diagnostic for a summary, but only when the
    summary does not already have an unresolved one. This keeps surfacing
    idempotent server-side, so the caller does not need to read diagnostics.

    Args:
        diagnostics_db: DatabaseManager for the diagnostics database.

    Returns:
        Async handler function.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Insert a pending-conflict row unless one already exists."""
        payload = ConflictedSummaryFlagParams.model_validate(params or {})
        cursor = diagnostics_db.execute(
            """
            INSERT INTO summary_processing_diagnostics
                (decision, summary_id, main_point)
            SELECT ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM summary_processing_diagnostics
                WHERE decision = ? AND summary_id = ?
            )
            """,
            (
                TRACE_DECISION_CONFLICT_PENDING,
                payload.summary_id,
                payload.main_point,
                TRACE_DECISION_CONFLICT_PENDING,
                payload.summary_id,
            ),
        )
        diagnostics_db.commit()
        return {"surfaced": int(cursor.rowcount or 0)}

    return handle


def make_list_memory_route_diagnostics_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for reading structured memory-route diagnostics.

    Args:
        diagnostics_db: Open diagnostics database manager.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List recent route diagnostics with grouped signal matches."""
        payload = ListMemoryRouteDiagnosticsParams.model_validate(params or {})
        query_sql, query_params = _memory_route_list_query(payload)
        rows = diagnostics_db.execute(query_sql, query_params).fetchall()
        diagnostic_ids = [int(row[0]) for row in rows]
        signal_groups_by_diagnostic = _load_signal_groups_by_diagnostic(
            diagnostics_db,
            diagnostic_ids,
        )
        diagnostics = [
            _memory_route_diagnostic_entry(
                row,
                signal_groups_by_diagnostic.get(
                    int(row[0]),
                    {"initial": [], "final": []},
                ),
            )
            for row in rows
        ]
        return {"diagnostics": diagnostics}

    return handle


def make_list_record_processing_diagnostics_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for reading record-processing diagnostics."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = ListRecordProcessingDiagnosticsParams.model_validate(params or {})
        query, query_params = _record_processing_query(payload)
        rows = diagnostics_db.execute(query, tuple(query_params)).fetchall()
        return {"entries": [_record_processing_entry(row) for row in rows]}

    return handle


def make_list_summary_processing_diagnostics_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for reading summary-processing diagnostics."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        payload = ListSummaryProcessingDiagnosticsParams.model_validate(params or {})
        query, query_params = _summary_processing_query(payload)
        rows = diagnostics_db.execute(query, tuple(query_params)).fetchall()
        return {"entries": [_summary_processing_entry(row) for row in rows]}

    return handle


def make_get_memory_route_diagnostics_summary_handler(
    diagnostics_db: DatabaseManager,
) -> OperationHandler:
    """Create handler for aggregated route-diagnostics summary data."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return summary counts and breakdowns for route diagnostics."""
        payload = GetMemoryRouteDiagnosticsSummaryParams.model_validate(params or {})
        _, query_params = _memory_route_summary_window_params(payload)
        totals_row = _memory_route_summary_totals(
            diagnostics_db,
            payload.recent_hours,
            query_params,
        )
        mismatch_rows = _memory_route_summary_count_rows(
            diagnostics_db,
            "mismatch_label",
            payload.recent_hours,
            query_params,
        )
        final_intent_rows = _memory_route_summary_count_rows(
            diagnostics_db,
            "final_intent",
            payload.recent_hours,
            query_params,
        )
        fallback_reason_rows = _memory_route_fallback_reason_rows(
            diagnostics_db,
            payload.recent_hours,
            query_params,
        )

        return {
            "totals": {
                "total_rows": int(totals_row[0] or 0),
                "fallback_rows": int(totals_row[1] or 0),
                "mismatch_rows": int(totals_row[2] or 0),
                "long_term_rows": int(totals_row[3] or 0),
            },
            "recent_hours": payload.recent_hours,
            "mismatch_labels": _label_count_entries(mismatch_rows, "label"),
            "final_intents": _label_count_entries(final_intent_rows, "intent"),
            "fallback_reasons": _label_count_entries(
                fallback_reason_rows,
                "reason",
                default_value="unknown",
            ),
        }

    return handle
