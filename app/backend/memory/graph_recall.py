"""Graph-backed recall: entry-point search, one-hop expansion, budgets.

Stage-3 consumption layer over the graph storage tier. Read-only: every
call goes through the existing stage-1 graph read operations on the
caller's DB-proxy client (Socket A for the orchestrator, Socket B for the
web tier). All functions degrade to empty results, never raise on an
empty graph.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.memory.context_builder.constants import estimate_tokens
from backend.memory.evidence_chain_models import EvidenceBundle


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)

# The migration placeholder entity is a staging node, not knowledge.
HIDDEN_ENTITY_TYPES = ("unresolved",)

GRAPH_RECALL_ENTRY_LIMIT = 8
GRAPH_RECALL_PER_ENTITY_LIMIT = 5
TRIM_MARKER = "(further results trimmed)"

_USER_ENTITY_TYPE = "person"
_USER_ENTITY_NAME = "user"

# Bounded fallback scans for evidence lookups: there are no get-by-id
# operations, and this stage adds none — cited rows are filtered out of
# one bounded list read instead.
_EVIDENCE_RECORD_SCAN_LIMIT = 1000
_EVIDENCE_MESSAGE_SCAN_LIMIT = 2000
_EVIDENCE_MAX_RECORDS = 3
_EVIDENCE_MAX_MESSAGES = 6


def _assertion_date(assertion: dict[str, Any]) -> str:
    """Return the fact's date: validity start when known, else creation."""
    raw = assertion.get("valid_from") or assertion.get("created_at") or ""
    return str(raw)[:10]


def _assertion_line(assertion: dict[str, Any]) -> str:
    """Format one assertion as a recall output line."""
    date_part = _assertion_date(assertion)
    prefix = f"[{date_part}] " if date_part else ""
    trust = assertion.get("trust_level", "medium")
    relation = assertion.get("relation", "")
    text = str(assertion.get("assertion_text", "")).strip()
    return f"- {prefix}[{trust}] {text} ({relation})"


async def build_graph_recall_lines(
    client: DBProxyClient,
    query: str,
    *,
    entry_limit: int = GRAPH_RECALL_ENTRY_LIMIT,
    per_entity_limit: int = GRAPH_RECALL_PER_ENTITY_LIMIT,
) -> list[str]:
    """Build the graph section of recall output for one query.

    Entry-point search over entities and assertions, then one traversal
    hop: each matching entity contributes its currently-valid assertions.
    Assertions are deduplicated by id; the section is capped at
    ``entry_limit`` lines.
    """
    entry_points = await client.find_graph_entry_points(query, entry_limit)
    seen_assertion_ids: set[str] = set()
    collected: list[dict[str, Any]] = []
    for hit in entry_points:
        if hit.get("kind") == "assertion":
            if hit.get("invalid_from") is not None:
                continue
            if hit.get("id") not in seen_assertion_ids:
                seen_assertion_ids.add(hit.get("id"))
                collected.append(hit)
            continue
        if hit.get("kind") != "entity":
            continue
        if hit.get("entity_type") in HIDDEN_ENTITY_TYPES:
            continue
        assertions = await client.list_assertions_for_entity(
            hit.get("id"),
            valid_only=True,
            limit=per_entity_limit,
        )
        for assertion in assertions:
            if assertion.get("id") not in seen_assertion_ids:
                seen_assertion_ids.add(assertion.get("id"))
                collected.append(assertion)
    return [_assertion_line(assertion) for assertion in collected[:entry_limit]]


def trim_lines_to_token_budget(text: str, budget_tokens: int) -> str:
    """Trim multi-line text to a token budget on line boundaries.

    Keeps at least one line; appends a trim marker when lines were
    dropped.
    """
    if estimate_tokens(text) <= budget_tokens:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    used = estimate_tokens(TRIM_MARKER)
    for line in lines:
        line_tokens = estimate_tokens(line)
        if kept and used + line_tokens > budget_tokens:
            break
        kept.append(line)
        used += line_tokens
        if used > budget_tokens:
            break
    kept.append(TRIM_MARKER)
    return "\n".join(kept)


async def build_user_fact_lines(client: DBProxyClient) -> list[str]:
    """List the user entity's currently-valid facts with dates.

    Returns an empty list when the graph has no user entity or no valid
    assertions about it.
    """
    try:
        candidates = await client.find_entities(_USER_ENTITY_NAME, 10)
    except Exception:
        logger.warning("Graph user-entity lookup failed", exc_info=True)
        return []
    user_entity = next(
        (
            entity
            for entity in candidates
            if entity.get("entity_type") == _USER_ENTITY_TYPE
            and entity.get("canonical_name") == _USER_ENTITY_NAME
        ),
        None,
    )
    if user_entity is None:
        return []
    assertions = await client.list_assertions_for_entity(
        user_entity.get("id"),
        valid_only=True,
    )
    subject_side = [
        assertion for assertion in assertions if assertion.get("direction") == "subject"
    ]
    return [_assertion_line(assertion) for assertion in subject_side]


async def load_graph_evidence_bundle(
    client: DBProxyClient,
    query: str,
) -> EvidenceBundle | None:
    """Build an evidence bundle from the best-matching graph assertion.

    Chain: assertion → its evidence records → those records' source
    messages. Returns None when no assertion matches the query.
    """
    entry_points = await client.find_graph_entry_points(query, 5)
    assertion_hits = [
        hit
        for hit in entry_points
        if hit.get("kind") == "assertion" and hit.get("invalid_from") is None
    ]
    if not assertion_hits:
        return None
    best = assertion_hits[0]
    cited_record_ids = {
        str(record_id) for record_id in best.get("source_record_ids", [])
    }
    records: list[dict[str, Any]] = []
    if cited_record_ids:
        all_records = await client.list_records(
            limit=_EVIDENCE_RECORD_SCAN_LIMIT,
        )
        records = [
            record
            for record in all_records
            if str(record.get("id")) in cited_record_ids
        ][:_EVIDENCE_MAX_RECORDS]
    message_ids: set[str] = set()
    for record in records:
        for message_id in record.get("source_message_ids", []):
            message_ids.add(str(message_id))
    source_messages: list[dict[str, Any]] = []
    if message_ids:
        all_messages = await client.list_messages(
            limit=_EVIDENCE_MESSAGE_SCAN_LIMIT,
        )
        source_messages = [
            message for message in all_messages if str(message.get("id")) in message_ids
        ][:_EVIDENCE_MAX_MESSAGES]
    return EvidenceBundle(
        summary={"main_point": best.get("assertion_text", "")},
        records=records,
        source_messages=source_messages,
    )
