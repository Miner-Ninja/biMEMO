"""Graph extraction pipeline: records → LLM → entities and assertions.

Called from the extractor background loop with one batch of unextracted
records. All storage writes go through the orchestrator's Socket-A client
(stage-1 graph operations). A model-call failure propagates so the loop
leaves the batch eligible for the next cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.security.content_filter import filter_output

from .constants import (
    GRAPH_EXTRACTOR_MAX_TOKENS,
    GRAPH_EXTRACTOR_SYSTEM_PROMPT,
    GRAPH_EXTRACTOR_TEMPERATURE,
)
from .parsing import ExtractedAssertion, parse_graph_extraction
from .rules import (
    CONFLICT_INVALIDATE,
    CONFLICT_SKIP_DUPLICATE,
    is_forbidden_entity_name,
    normalize_relation,
    resolve_conflict_action,
)
from .trace import (
    TRACE_DECISION_CONFLICT_KEPT,
    TRACE_DECISION_DUPLICATE,
    TRACE_DECISION_FORBIDDEN_ENTITY,
    TRACE_DECISION_INVALIDATED,
    TRACE_DECISION_PARSE_EMPTY,
    TRACE_DECISION_STORED,
    TRACE_DECISION_SUBJECT_UNDECLARED,
    TRACE_DECISION_UNSAFE_OUTPUT,
    write_graph_extractor_trace,
)


if TYPE_CHECKING:
    from backend.llm.ollama.provider import OllamaProvider
    from backend.llm.openai_compatible.provider import OpenAICompatibleProvider
    from backend.memory.runtime.event_bus import MemoryEventBus
    from backend.processes.orchestrator.client import DBProxyClient


logger = logging.getLogger(__name__)


def _records_prompt(records: list[dict[str, Any]]) -> str:
    """Render one batch of records into the extraction user prompt."""
    lines = []
    for record in records:
        lines.append(f"- {record.get('summary', '')}")
        for fact in record.get("key_facts", []):
            lines.append(f"  fact: {fact}")
    return "Conversation records:\n" + "\n".join(lines)


async def _store_assertion(
    *,
    db_client: DBProxyClient,
    assertion: ExtractedAssertion,
    entity_ids: dict[str, str],
    record_ids: list[str],
) -> str | None:
    """Store one extracted assertion after conflict resolution."""
    subject_id = entity_ids.get(assertion.subject_name.strip().casefold())
    if subject_id is None:
        write_graph_extractor_trace(
            TRACE_DECISION_SUBJECT_UNDECLARED,
            {"subject": assertion.subject_name, "text": assertion.text},
        )
        return None
    if filter_output(assertion.text).injection_flagged:
        write_graph_extractor_trace(
            TRACE_DECISION_UNSAFE_OUTPUT,
            {"text": assertion.text[:200]},
        )
        return None
    relation = normalize_relation(assertion.relation)
    object_entity_id = None
    if assertion.object_entity_name is not None:
        object_entity_id = entity_ids.get(
            assertion.object_entity_name.strip().casefold()
        )
        if object_entity_id is None:
            object_entity_id = await db_client.save_entity(
                "topic",
                assertion.object_entity_name,
            )
            entity_ids[assertion.object_entity_name.strip().casefold()] = (
                object_entity_id
            )
    existing_valid = await db_client.list_assertions_for_entity(
        subject_id,
        valid_only=True,
    )
    subject_side = [
        entry for entry in existing_valid if entry.get("direction") == "subject"
    ]
    action, existing_id = resolve_conflict_action(
        new_relation=relation,
        new_trust=assertion.trust,
        new_object_entity_id=object_entity_id,
        new_object_value=assertion.object_value,
        existing_valid=subject_side,
    )
    if action == CONFLICT_SKIP_DUPLICATE:
        write_graph_extractor_trace(
            TRACE_DECISION_DUPLICATE,
            {"text": assertion.text, "existing": existing_id},
        )
        return None
    assertion_id = await db_client.save_assertion(
        subject_entity_id=subject_id,
        relation=relation,
        assertion_text=assertion.text,
        object_entity_id=object_entity_id,
        object_value=assertion.object_value if object_entity_id is None else None,
        trust_level=assertion.trust,
        assertion_scope=assertion.scope,
        source_record_ids=record_ids,
    )
    if action == CONFLICT_INVALIDATE and existing_id is not None:
        await db_client.invalidate_assertion(existing_id, assertion_id)
        write_graph_extractor_trace(
            TRACE_DECISION_INVALIDATED,
            {"new": assertion_id, "invalidated": existing_id},
        )
    elif existing_id is not None:
        write_graph_extractor_trace(
            TRACE_DECISION_CONFLICT_KEPT,
            {"new": assertion_id, "existing": existing_id},
        )
    else:
        write_graph_extractor_trace(
            TRACE_DECISION_STORED,
            {"assertion_id": assertion_id, "relation": relation},
        )
    return assertion_id


async def process_graph_extraction(
    provider: OllamaProvider | OpenAICompatibleProvider,
    db_client: DBProxyClient,
    records: list[dict[str, Any]],
    event_bus: MemoryEventBus | None = None,
) -> int:
    """Extract one batch of records into graph entities and assertions.

    Returns the number of stored assertions. Model-call exceptions
    propagate to the caller: the records stay unextracted and are picked
    up by a later cycle.
    """
    if not records:
        return 0
    response = await provider.chat(
        [
            _chat_message("system", GRAPH_EXTRACTOR_SYSTEM_PROMPT),
            _chat_message("user", _records_prompt(records)),
        ],
        model=provider.dialog_recorder_model,
        max_tokens=GRAPH_EXTRACTOR_MAX_TOKENS,
        temperature=GRAPH_EXTRACTOR_TEMPERATURE,
        enable_thinking=False,
    )
    extraction = parse_graph_extraction(response.content or "")
    if not extraction.entities and not extraction.assertions:
        write_graph_extractor_trace(
            TRACE_DECISION_PARSE_EMPTY,
            {
                "raw_output": (response.content or "")[:500],
                "discarded": extraction.discarded_lines[:10],
            },
        )
        return 0

    entity_ids: dict[str, str] = {}
    for entity in extraction.entities:
        if is_forbidden_entity_name(entity.name):
            write_graph_extractor_trace(
                TRACE_DECISION_FORBIDDEN_ENTITY,
                {"name": entity.name},
            )
            continue
        entity_id = await db_client.save_entity(
            entity.entity_type,
            entity.name,
        )
        entity_ids[entity.name.strip().casefold()] = entity_id

    record_ids = [str(record.get("id", "")) for record in records]
    stored_count = 0
    for assertion in extraction.assertions:
        assertion_id = await _store_assertion(
            db_client=db_client,
            assertion=assertion,
            entity_ids=entity_ids,
            record_ids=record_ids,
        )
        if assertion_id is not None:
            stored_count += 1
            if event_bus is not None:
                await event_bus.publish(
                    {
                        "type": "graph_extractor",
                        "assertion_id": assertion_id,
                    }
                )
    return stored_count


def _chat_message(role: str, content: str) -> object:
    """Build one provider chat message."""
    from backend.llm.ollama.models import ChatMessage

    return ChatMessage(role=role, content=content)
