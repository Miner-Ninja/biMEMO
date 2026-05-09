"""Tool handler registration.

Registers all 10 built-in tool handlers with the ToolBroker:
4 core handlers via DB Proxy IPC and 6 worker-proxy handlers.
Also registers ToolDefinition objects in the ToolRegistry.
"""

import logging
from typing import TYPE_CHECKING

from backend.memory.evidence_chain import load_evidence_bundle_for_query
from backend.memory.evidence_recall import is_evidence_recall_question
from backend.tools.builtin import (
    EmailSendInput,
    FileReadInput,
    FileSearchInput,
    FileWriteInput,
    HttpFetchInput,
    MemoryRecallInput,
    ScheduleTaskInput,
    SkillCreateInput,
    SkillLoadInput,
    SkillSearchInput,
    SystemStatusInput,
    WebSearchInput,
)
from backend.tools.handlers.schedule import make_schedule_task_handler
from backend.tools.handlers.skill import (
    make_skill_create_handler,
    make_skill_load_handler,
    make_skill_search_handler,
)
from backend.tools.handlers.system_status import (
    make_system_status_handler,
)
from backend.tools.handlers.worker_proxy import (
    make_email_proxy,
    make_worker_proxy,
)
from backend.tools.registry import ToolDefinition, ToolRegistry, WorkerTarget
from backend.trust.zones import TrustZone


if TYPE_CHECKING:
    from backend.bus.worker_manager import WorkerManager
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)

MEMORY_RECALL_RESULT_LIMIT = 5
NO_QUERY_PROVIDED_TEXT = "No query provided."
NO_RELEVANT_MEMORIES_TEXT = "No relevant memories found."
SUMMARY_SECTION_TITLE = "Summary memory:"
RECORDS_SECTION_TITLE = "Linked records:"
SOURCE_MESSAGES_SECTION_TITLE = "Source messages:"


TOOL_DEFINITIONS = (
    ToolDefinition(
        name="file_read",
        description="Read a file within allowed directories",
        input_model=FileReadInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.FS_WORKER,
    ),
    ToolDefinition(
        name="file_write",
        description="Write content to a file within allowed directories",
        input_model=FileWriteInput,
        zone=TrustZone.YELLOW,
        worker=WorkerTarget.FS_WORKER,
    ),
    ToolDefinition(
        name="file_search",
        description="Search for files matching a pattern within allowed directories",
        input_model=FileSearchInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.FS_WORKER,
    ),
    ToolDefinition(
        name="web_search",
        description="Search the web via local SearXNG instance",
        input_model=WebSearchInput,
        zone=TrustZone.YELLOW,
        worker=WorkerTarget.NET_WORKER,
    ),
    ToolDefinition(
        name="memory_recall",
        description=(
            "Search past conversations and memories. "
            "Use when asked about prior discussions, "
            "user preferences, or past decisions."
        ),
        input_model=MemoryRecallInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="http_fetch",
        description="Fetch a URL (HTTPS only, with SSRF protection)",
        input_model=HttpFetchInput,
        zone=TrustZone.RED,
        worker=WorkerTarget.NET_WORKER,
    ),
    ToolDefinition(
        name="email_send",
        description="Send an email via SMTP",
        input_model=EmailSendInput,
        zone=TrustZone.RED,
        worker=WorkerTarget.NET_WORKER,
    ),
    ToolDefinition(
        name="schedule_task",
        description=(
            "Manage scheduled tasks. Actions: create (name, prompt, schedule, "
            "task_type, allowed_tools), list, pause (task_id), "
            "resume (task_id), remove (task_id). "
            "Schedule formats: ISO timestamp, interval (30m, 2h, 1d), "
            "or cron expression (0 9 * * *)."
        ),
        input_model=ScheduleTaskInput,
        zone=TrustZone.YELLOW,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="system_status",
        description="Show system metrics: disk, RAM, workers, uptime",
        input_model=SystemStatusInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="skill_create",
        description=(
            "Save a reusable skill recipe after solving a complex task. "
            "Include step-by-step procedure and tools used."
        ),
        input_model=SkillCreateInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="skill_search",
        description="Search for a relevant saved skill before starting a task",
        input_model=SkillSearchInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="skill_load",
        description="Load the full procedure of a saved skill by name",
        input_model=SkillLoadInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
)


def register_tool_definitions(registry: ToolRegistry) -> None:
    """Register all built-in tool definitions in the registry.

    Args:
        registry: Tool registry to populate.
    """
    for tool_def in TOOL_DEFINITIONS:
        registry.register(tool_def)
    logger.info("Registered %d tool definitions", len(TOOL_DEFINITIONS))


def _register_core_handlers(
    broker: ToolBroker,
    *,
    db_client: object,
    data_dir: str,
    ready_since: float,
    get_ollama_status: callable,
    get_worker_count: callable,
    db_path: str | None,
) -> None:
    """Register built-in handlers that run in the main process."""
    broker.set_handler(
        "schedule_task",
        make_schedule_task_handler(db_client, broker=broker),
    )
    broker.set_handler(
        "system_status",
        make_system_status_handler(
            data_dir=data_dir,
            ready_since=ready_since,
            get_ollama_status=get_ollama_status,
            get_worker_count=get_worker_count,
            db_path=db_path,
        ),
    )
    broker.set_handler(
        "memory_recall",
        _make_memory_recall_handler(db_client),
    )


# --- Memory recall stats (in-process, resets on restart) ---
_recall_stats: dict[str, int] = {"total_recalls": 0, "total_results": 0}


def get_recall_stats() -> dict[str, object]:
    """Return current memory_recall invocation stats."""
    total = _recall_stats["total_recalls"]
    results = _recall_stats["total_results"]
    return {
        "total_recalls": total,
        "total_results": results,
        "avg_results_per_recall": (round(results / total, 1) if total > 0 else 0),
    }


def _clean_memory_text(value: object) -> str:
    """Return a stripped string for memory recall formatting."""
    return str(value if value is not None else "").strip()


def _append_bullet_section(
    lines: list[str],
    *,
    title: str,
    items: list[str],
) -> None:
    """Append one titled bullet section when the item list is not empty."""
    if not items:
        return
    lines.append(title)
    lines.extend(f"- {item}" for item in items)


def _record_result_count(parts: list[str]) -> None:
    """Update in-process recall metrics from one rendered result list."""
    _recall_stats["total_recalls"] += 1
    _recall_stats["total_results"] += len(parts)


def _fast_recall_parts(
    *,
    records: list[dict[str, object]],
    messages: list[dict[str, object]],
    max_message_chars: int,
) -> list[str]:
    """Build the current flat memory recall output from FTS results."""
    parts: list[str] = []
    for record in records:
        summary = _clean_memory_text(record.get("summary", ""))
        if summary:
            parts.append(f"- {summary}")
    for message in messages:
        role = _clean_memory_text(message.get("role", ""))
        content = _clean_memory_text(message.get("content", ""))
        if not content:
            continue
        snippet = content[:max_message_chars]
        parts.append(f"- [{role}] {snippet}")
    return parts


async def _build_fast_recall_result(
    *,
    db_client: object,
    query: str,
    max_message_chars: int,
) -> str:
    """Run the existing lightweight memory recall search."""
    records = await db_client.fulltext_search(
        query=query,
        table="records",
        limit=MEMORY_RECALL_RESULT_LIMIT,
    )
    messages = await db_client.fulltext_search(
        query=query,
        table="messages",
        limit=MEMORY_RECALL_RESULT_LIMIT,
    )
    parts = _fast_recall_parts(
        records=records,
        messages=messages,
        max_message_chars=max_message_chars,
    )
    _record_result_count(parts)
    if not parts:
        return NO_RELEVANT_MEMORIES_TEXT
    return "\n".join(parts)


def _evidence_record_summaries(records: list[dict[str, object]]) -> list[str]:
    """Extract visible record summaries for evidence-backed recall output."""
    return [
        summary
        for record in records
        if (summary := _clean_memory_text(record.get("summary", "")))
    ]


def _evidence_source_messages(
    messages: list[dict[str, object]],
) -> list[str]:
    """Format source messages for evidence-backed recall output."""
    formatted_messages: list[str] = []
    for message in messages:
        content = _clean_memory_text(message.get("content", ""))
        if not content:
            continue
        role = _clean_memory_text(message.get("role", "unknown")) or "unknown"
        created_at = _clean_memory_text(message.get("created_at", ""))
        if created_at:
            formatted_messages.append(f"{created_at} {role}: {content}")
            continue
        formatted_messages.append(f"{role}: {content}")
    return formatted_messages


def _build_evidence_result(bundle: object) -> str:
    """Format one evidence bundle into tool output text."""
    summary = getattr(bundle, "summary", {})
    records = getattr(bundle, "records", [])
    source_messages = getattr(bundle, "source_messages", [])
    lines: list[str] = []
    summary_text = _clean_memory_text(summary.get("main_point", ""))
    if summary_text:
        lines.extend((SUMMARY_SECTION_TITLE, f"- {summary_text}"))
    _append_bullet_section(
        lines,
        title=RECORDS_SECTION_TITLE,
        items=_evidence_record_summaries(records),
    )
    _append_bullet_section(
        lines,
        title=SOURCE_MESSAGES_SECTION_TITLE,
        items=_evidence_source_messages(source_messages),
    )
    return "\n".join(lines).strip()


async def _build_evidence_recall_result(
    *,
    db_client: object,
    query: str,
    max_message_chars: int,
) -> str:
    """Run source-backed memory recall and fall back to fast recall if needed."""
    evidence_bundle = await load_evidence_bundle_for_query(query, db_client)
    if evidence_bundle is None:
        return await _build_fast_recall_result(
            db_client=db_client,
            query=query,
            max_message_chars=max_message_chars,
        )
    evidence_result = _build_evidence_result(evidence_bundle)
    if not evidence_result:
        return await _build_fast_recall_result(
            db_client=db_client,
            query=query,
            max_message_chars=max_message_chars,
        )
    result_items = [
        line for line in evidence_result.splitlines() if line.startswith("- ")
    ]
    _record_result_count(result_items)
    return evidence_result


def _make_memory_recall_handler(db_client: object) -> callable:
    """Build an async handler for the memory_recall tool."""
    from backend.memory.context_builder.constants import MAX_RETRIEVED_CONTENT_CHARS

    async def handler(params: object) -> dict:
        """Search past conversations and return formatted results."""
        query = getattr(params, "query", "") or ""
        if not query:
            return {"result": NO_QUERY_PROVIDED_TEXT}
        if is_evidence_recall_question(query):
            result = await _build_evidence_recall_result(
                db_client=db_client,
                query=query,
                max_message_chars=MAX_RETRIEVED_CONTENT_CHARS,
            )
            return {"result": result}
        result = await _build_fast_recall_result(
            db_client=db_client,
            query=query,
            max_message_chars=MAX_RETRIEVED_CONTENT_CHARS,
        )
        return {"result": result}

    return handler


def _register_worker_proxy_group(
    broker: ToolBroker,
    worker: WorkerManager,
    tool_names: tuple[str, ...],
) -> None:
    """Register one group of worker-proxy handlers."""
    for tool_name in tool_names:
        broker.set_handler(
            tool_name,
            make_worker_proxy(worker, tool_name),
        )


def _worker_proxy_tools() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the built-in FS and NET worker tool groups."""
    return (
        ("file_read", "file_write", "file_search"),
        ("web_search", "http_fetch"),
    )


def register_all_handlers(
    broker: ToolBroker,
    db_client: object,
    fs_worker: WorkerManager,
    net_worker: WorkerManager,
    data_dir: str,
    ready_since: float,
    get_ollama_status: callable,
    get_worker_count: callable,
    db_path: str | None = None,
    smtp_config: dict | None = None,
) -> None:
    """Register all 12 tool handlers with the broker.

    Args:
        broker: Tool broker to register handlers with.
        db_client: DBProxyClient for core handlers (IPC-based).
        fs_worker: FS Worker manager for file operations.
        net_worker: Net Worker manager for network operations.
        data_dir: Data directory path for system_status.
        ready_since: Timestamp for uptime calculation.
        get_ollama_status: Callable returning Ollama status.
        get_worker_count: Callable returning worker count.
        db_path: Path to the SQLCipher database file.
        smtp_config: SMTP configuration dict for email_send.
            None if SMTP not configured.
    """
    _register_core_handlers(
        broker,
        db_client=db_client,
        data_dir=data_dir,
        ready_since=ready_since,
        get_ollama_status=get_ollama_status,
        get_worker_count=get_worker_count,
        db_path=db_path,
    )

    fs_tools, net_tools = _worker_proxy_tools()
    _register_worker_proxy_group(broker, fs_worker, fs_tools)
    _register_worker_proxy_group(broker, net_worker, net_tools)

    broker.set_handler(
        "email_send",
        make_email_proxy(net_worker, smtp_config),
    )

    broker.set_handler(
        "skill_create",
        make_skill_create_handler(db_client),
    )
    broker.set_handler(
        "skill_search",
        make_skill_search_handler(db_client),
    )
    broker.set_handler(
        "skill_load",
        make_skill_load_handler(db_client),
    )

    logger.info("All 11 tool handlers registered")


EXPECTED_TOOLS = frozenset(
    {
        "system_status",
        "file_read",
        "file_write",
        "file_search",
        "web_search",
        "http_fetch",
        "email_send",
        "schedule_task",
        "skill_create",
        "skill_search",
        "skill_load",
    }
)


def validate_all_registered(
    broker: ToolBroker,
) -> None:
    """Verify all 12 expected tool handlers are registered.

    Args:
        broker: Tool broker to validate.

    Raises:
        RuntimeError: If any expected handler is missing.
    """
    registered = set(broker._handlers.keys())
    missing = EXPECTED_TOOLS - registered
    if missing:
        missing_tools = ", ".join(sorted(missing))
        raise RuntimeError(f"Missing tool handlers: {missing_tools}")
