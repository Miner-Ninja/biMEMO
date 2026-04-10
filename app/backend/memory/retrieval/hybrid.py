"""Hybrid retrieval helpers for memory search results."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.memory.retrieval.fulltext import (
    search_messages,
    search_observations,
    search_reflections,
)
from backend.memory.retrieval.vector_store import search_similar


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

_RESOLVE_SQL = {
    "messages": "SELECT id FROM messages WHERE rowid = ?",
    "records": ("SELECT id FROM records WHERE rowid = ?"),
    "summaries": "SELECT id FROM summaries WHERE rowid = ?",
}


def _resolve_id(db: DatabaseManager, table: str, rowid: int) -> str | None:
    """Resolve an FTS rowid to the source table string ID."""
    sql = _RESOLVE_SQL.get(table)
    if sql is None:
        return None
    row = db.execute(sql, (rowid,)).fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class SearchResult:
    """A single search result with source tracking."""

    identifier: str | int
    source: str
    score: float


def _rrf_score(rank: int, k: int = 60) -> float:
    """Compute Reciprocal Rank Fusion score for a given rank."""
    return 1.0 / (k + rank)


def _keyword_search_rows(
    db: DatabaseManager,
    table: str,
    query_text: str,
    limit: int,
) -> list[tuple[object, ...]]:
    """Return ranked full-text rows for one supported source table."""
    if table == "messages":
        return search_messages(db, query_text, limit)
    if table == "records":
        return search_observations(db, query_text, limit)
    if table == "summaries":
        return search_reflections(db, query_text, limit)
    raise ValueError(f"Unsupported search table: {table}")


def _merge_ranked_score(
    scores: dict[str | int, float],
    sources: dict[str | int, set[str]],
    identifier: str | int,
    source: str,
    rank_pos: int,
) -> None:
    """Merge one ranked result into the shared hybrid score maps."""
    scores[identifier] = scores.get(identifier, 0.0) + _rrf_score(rank_pos)
    sources.setdefault(identifier, set()).add(source)


def _build_search_results(
    scores: dict[str | int, float],
    sources: dict[str | int, set[str]],
    limit: int,
) -> list[SearchResult]:
    """Build the final ranked result list from merged scores."""
    results = []
    for doc_id, score in scores.items():
        doc_sources = sources[doc_id]
        source = "hybrid" if len(doc_sources) > 1 else next(iter(doc_sources))
        results.append(SearchResult(identifier=doc_id, source=source, score=score))

    results.sort(key=lambda result: result.score, reverse=True)
    return results[:limit]


def _keyword_result_identifier(
    db: DatabaseManager,
    *,
    table: str,
    row: tuple[object, ...],
) -> str | None:
    """Resolve the source identifier for one keyword-search row."""
    return _resolve_id(db, table, row[0])


def hybrid_search(
    db: DatabaseManager,
    query_text: str,
    query_embedding: list[float] | None,
    table: str,
    limit: int = 10,
) -> list[SearchResult]:
    """Run combined vector plus keyword search with RRF merging."""
    scores: dict[str | int, float] = {}
    sources: dict[str | int, set[str]] = {}

    if query_embedding is not None and table == "records":
        vec_results = search_similar(db, query_embedding, limit)
        for rank_pos, (obs_id, _distance) in enumerate(vec_results, start=1):
            _merge_ranked_score(scores, sources, obs_id, "vector", rank_pos)

    fts_rows = _keyword_search_rows(db, table, query_text, limit)

    for rank_pos, row in enumerate(fts_rows, start=1):
        str_id = _keyword_result_identifier(
            db,
            table=table,
            row=row,
        )
        if str_id is not None:
            _merge_ranked_score(scores, sources, str_id, "keyword", rank_pos)

    return _build_search_results(scores, sources, limit)
