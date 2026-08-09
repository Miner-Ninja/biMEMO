"""Tool handler registration.

Registers all 10 built-in tool handlers with the ToolBroker:
4 core handlers via DB Proxy IPC and 6 worker-proxy handlers.
Also registers ToolDefinition objects in the ToolRegistry.
"""

import logging
from typing import TYPE_CHECKING

from backend.memory.evidence_recall import is_evidence_recall_question
from backend.tools.builtin import (
    CodeSearchInput,
    EmailSendInput,
    ExecuteCommandInput,
    ExecuteCommandStreamInput,
    FileReadInput,
    FileSearchInput,
    FileWriteInput,
    GitCommitInput,
    GitDiffInput,
    GitLogInput,
    GitPushInput,
    GitStatusInput,
    HttpFetchInput,
    MemoryRecallInput,
    ScheduleTaskInput,
    SetWorkspaceInput,
    SkillCreateInput,
    SkillLoadInput,
    SkillSearchInput,
    SystemStatusInput,
    WebSearchInput,
    WorkspaceHistoryInput,
    WorkspaceNoteInput,
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

# Late-bound query-embedding callable, set by the orchestrator once vector
# support is probed (None when no embedding model is configured). Recall
# reads it at call time so hybrid vector recall lights up without threading
# the callable through the whole tool-registration chain.
_recall_embed_fn: object | None = None


def set_recall_embed_fn(embed_fn: object | None) -> None:
    """Register (or clear) the query-embedding callable used by recall."""
    global _recall_embed_fn
    _recall_embed_fn = embed_fn
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
    ToolDefinition(
        name="set_workspace",
        description=(
            "Manage workspace directories. "
            "Actions: add (path), remove (path), list. "
            "Add a directory to allow file operations there. "
            "Remove to revoke access. List to see current allowed dirs."
        ),
        input_model=SetWorkspaceInput,
        zone=TrustZone.YELLOW,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="workspace_history",
        description=(
            "Get or clear workspace action history. "
            "Use 'get' to see recent file/command actions. "
            "Use 'clear' to reset history."
        ),
        input_model=WorkspaceHistoryInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="workspace_note",
        description=(
            "Add semantic notes to workspace history. "
            "Include progress updates, decisions made, or blockers. "
            "Optional fields: user_intent, progress, decisions (JSON), blockers (JSON)."
        ),
        input_model=WorkspaceNoteInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="git_status",
        description="Show git working tree status (branch, staged, unstaged, untracked).",
        input_model=GitStatusInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="git_diff",
        description="Show git diff (unstaged or staged changes).",
        input_model=GitDiffInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="git_log",
        description="Show git commit history (last N commits).",
        input_model=GitLogInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="git_commit",
        description="Commit changes with message. RED zone — requires confirmation.",
        input_model=GitCommitInput,
        zone=TrustZone.RED,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="git_push",
        description="Push to remote. RED zone — requires confirmation.",
        input_model=GitPushInput,
        zone=TrustZone.RED,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="code_search",
        description=(
            "Search for text or regex patterns across workspace files. "
            "Returns matching files with line numbers and context."
        ),
        input_model=CodeSearchInput,
        zone=TrustZone.GREEN,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="execute_command",
        description=(
            "Execute a shell command with allowlist security. "
            "Only whitelisted commands are allowed. "
            "Returns stdout, stderr, exit_code. "
            "Timeout defaults to 30s. "
            "RED zone — requires user confirmation."
        ),
        input_model=ExecuteCommandInput,
        zone=TrustZone.RED,
        worker=WorkerTarget.CORE,
    ),
    ToolDefinition(
        name="execute_command_stream",
        description=(
            "Execute a shell command with streaming output. "
            "Same as execute_command but for long-running processes. "
            "Output is collected and returned when done. "
            "RED zone — requires user confirmation."
        ),
        input_model=ExecuteCommandStreamInput,
        zone=TrustZone.RED,
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


def _memory_date_prefix(entry: dict[str, object]) -> str:
    """Return a ``[YYYY-MM-DD] `` prefix when the entry carries a date."""
    created_at = _clean_memory_text(entry.get("created_at", ""))
    if not created_at:
        return ""
    return f"[{created_at[:10]}] "


def _fast_recall_parts(
    *,
    summaries: list[dict[str, object]],
    records: list[dict[str, object]],
    messages: list[dict[str, object]],
    max_message_chars: int,
) -> list[str]:
    """Build the flat memory recall output from FTS results.

    Distilled summaries lead (highest-trust tier), then records, then
    raw message excerpts. Each line carries the entry date so the model
    can weigh recency.
    """
    parts: list[str] = []
    for summary in summaries:
        main_point = _clean_memory_text(summary.get("main_point", ""))
        if main_point:
            parts.append(f"- {_memory_date_prefix(summary)}{main_point}")
    for record in records:
        summary_text = _clean_memory_text(record.get("summary", ""))
        if summary_text:
            parts.append(f"- {_memory_date_prefix(record)}{summary_text}")
    for message in messages:
        role = _clean_memory_text(message.get("role", ""))
        content = _clean_memory_text(message.get("content", ""))
        if not content:
            continue
        excerpt = content[:max_message_chars]
        parts.append(f"- {_memory_date_prefix(message)}[{role}] {excerpt}")
    return parts


async def _fuse_vector_records(
    db_client: object,
    query: str,
    fts_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Fuse FTS record hits with semantic (vector) hits via RRF.

    When no embedding callable is registered, or embedding/vector search
    yields nothing, the FTS records pass through unchanged — recall then
    behaves exactly as before this feature.
    """
    embed_fn = _recall_embed_fn
    if embed_fn is None:
        return fts_records
    try:
        query_embedding = await embed_fn(query)
    except Exception:
        logger.warning("Query embedding failed; using FTS records only", exc_info=True)
        return fts_records
    if not query_embedding:
        return fts_records
    vector_records = await db_client.vector_search(
        query_embedding,
        MEMORY_RECALL_RESULT_LIMIT,
    )
    if not vector_records:
        return fts_records

    from backend.memory.retrieval.rrf import reciprocal_rank_fusion

    fused = reciprocal_rank_fusion(
        [fts_records, vector_records],
        key=lambda record: str(record.get("id", "")),
    )
    return fused[:MEMORY_RECALL_RESULT_LIMIT]


async def _build_fast_recall_result(
    *,
    db_client: object,
    query: str,
    max_message_chars: int,
) -> str:
    """Run the lightweight memory recall search.

    With graph memory enabled the graph section (dated, trust-labeled
    valid assertions) leads and the frozen summaries tier is skipped —
    its content lives in the graph. Records and messages remain the
    episodic tier in both modes.
    """
    from backend.memory.graph_recall import build_graph_recall_lines

    graph_lines = await build_graph_recall_lines(db_client, query)
    summaries: list[dict[str, object]] = []
    records = await db_client.fulltext_search(
        query=query,
        table="records",
        limit=MEMORY_RECALL_RESULT_LIMIT,
    )
    records = await _fuse_vector_records(db_client, query, records)
    messages = await db_client.fulltext_search(
        query=query,
        table="messages",
        limit=MEMORY_RECALL_RESULT_LIMIT,
    )
    parts = graph_lines + _fast_recall_parts(
        summaries=summaries,
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
    from backend.memory.graph_recall import load_graph_evidence_bundle

    evidence_bundle = await load_graph_evidence_bundle(db_client, query)
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


def _make_set_workspace_handler(workspace: object) -> callable:
    """Build an async handler for the set_workspace tool."""

    async def handler(params: object) -> dict:
        """Manage workspace directories."""
        action = getattr(params, "action", "") or ""
        path = getattr(params, "path", "") or ""

        if action == "list":
            return {"directories": workspace.get()}

        if action == "add":
            if not path:
                return {"error": "path is required for add action"}
            try:
                from pathlib import Path

                resolved = Path(path).resolve()
                if not resolved.is_dir():
                    return {"error": f"Directory does not exist: {path}"}
                workspace.add(str(resolved))
                return {
                    "status": "added",
                    "directory": str(resolved),
                    "all_directories": workspace.get(),
                }
            except Exception as e:
                return {"error": str(e)}

        if action == "remove":
            if not path:
                return {"error": "path is required for remove action"}
            removed = workspace.remove(path)
            if removed:
                return {
                    "status": "removed",
                    "directory": path,
                    "all_directories": workspace.get(),
                }
            return {"error": f"Directory not found in workspace: {path}"}

        return {"error": f"Unknown action: {action}. Use: add, remove, list"}

    return handler


def _make_workspace_history_handler(store: object) -> callable:
    """Build an async handler for the workspace_history tool."""

    async def handler(params: object) -> dict:
        """Get or clear workspace history."""
        action = getattr(params, "action", "") or ""
        # Get conversation_id from broker context
        conversation_id = "default"

        if action == "get":
            entries = await store.get_latest(conversation_id, last_n=10)
            return {
                "entries": [e.to_dict() for e in entries],
                "count": len(entries),
            }

        if action == "clear":
            await store.clear(conversation_id)
            return {"status": "cleared"}

        return {"error": f"Unknown action: {action}. Use: get, clear"}

    return handler


def _make_workspace_note_handler(
    tracker: object,
    store: object,
) -> callable:
    """Build an async handler for the workspace_note tool."""
    import json as _json

    async def handler(params: object) -> dict:
        """Add semantic notes to workspace history."""
        user_intent = getattr(params, "user_intent", "") or ""
        progress = getattr(params, "progress", "") or ""
        decisions_raw = getattr(params, "decisions", "[]") or "[]"
        blockers_raw = getattr(params, "blockers", "[]") or "[]"

        try:
            decisions = _json.loads(decisions_raw) if decisions_raw else []
        except Exception:
            decisions = [decisions_raw]
        try:
            blockers = _json.loads(blockers_raw) if blockers_raw else []
        except Exception:
            blockers = [blockers_raw]

        tracker.add_note(
            user_intent=user_intent,
            progress=progress,
            decisions=decisions if isinstance(decisions, list) else [decisions],
            blockers=blockers if isinstance(blockers, list) else [blockers],
        )

        return {"status": "note added"}

    return handler


def _make_execute_command_handler() -> callable:
    """Build an async handler for the execute_command tool."""

    async def handler(params: object) -> dict:
        """Execute a shell command and return output."""
        from backend.workers.cmd_worker import execute_command

        command = getattr(params, "command", "") or ""
        cwd = getattr(params, "cwd", "") or None
        timeout = getattr(params, "timeout", 30.0) or 30.0

        if not command:
            return {"error": "command is required"}

        result = await execute_command(
            command=command,
            cwd=cwd,
            timeout=timeout,
        )

        response: dict = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
        if result.error:
            response["error"] = result.error
        return response

    return handler


def _make_execute_command_stream_handler() -> callable:
    """Build an async handler for the execute_command_stream tool.

    Runs the command and returns output in chunks. For the MVP,
    this collects all output and returns it at once (true streaming
    requires WebSocket integration which is deferred).
    """

    async def handler(params: object) -> dict:
        """Execute a command with streaming output."""
        from backend.workers.cmd_worker import execute_command

        command = getattr(params, "command", "") or ""
        cwd = getattr(params, "cwd", "") or None
        timeout = getattr(params, "timeout", 30.0) or 30.0

        if not command:
            return {"error": "command is required"}

        result = await execute_command(
            command=command,
            cwd=cwd,
            timeout=timeout,
        )

        response: dict = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
        if result.error:
            response["error"] = result.error
        return response

    return handler


def _make_git_status_handler() -> callable:
    async def handler(params: object) -> dict:
        from backend.tools.handlers.git_ops import git_status

        cwd = getattr(params, "cwd", "") or None
        return await git_status(cwd=cwd)

    return handler


def _make_git_diff_handler() -> callable:
    async def handler(params: object) -> dict:
        from backend.tools.handlers.git_ops import git_diff

        cwd = getattr(params, "cwd", "") or None
        staged = getattr(params, "staged", False) or False
        return await git_diff(cwd=cwd, staged=staged)

    return handler


def _make_git_log_handler() -> callable:
    async def handler(params: object) -> dict:
        from backend.tools.handlers.git_ops import git_log

        cwd = getattr(params, "cwd", "") or None
        count = getattr(params, "count", 10) or 10
        return await git_log(cwd=cwd, count=count)

    return handler


def _make_git_commit_handler() -> callable:
    async def handler(params: object) -> dict:
        from backend.tools.handlers.git_ops import git_commit

        message = getattr(params, "message", "") or ""
        cwd = getattr(params, "cwd", "") or None
        all_files = getattr(params, "all_files", False) or False
        if not message:
            return {"error": "message is required"}
        return await git_commit(message=message, cwd=cwd, all_files=all_files)

    return handler


def _make_git_push_handler() -> callable:
    async def handler(params: object) -> dict:
        from backend.tools.handlers.git_ops import git_push

        cwd = getattr(params, "cwd", "") or None
        remote = getattr(params, "remote", "origin") or "origin"
        branch = getattr(params, "branch", "") or None
        return await git_push(cwd=cwd, remote=remote, branch=branch)

    return handler


def _make_code_search_handler() -> callable:
    async def handler(params: object) -> dict:
        from backend.tools.handlers.code_search import code_search

        pattern = getattr(params, "pattern", "") or ""
        cwd = getattr(params, "cwd", "") or None
        file_pattern = getattr(params, "file_pattern", "**/*") or "**/*"
        max_results = getattr(params, "max_results", 50) or 50
        context_lines = getattr(params, "context_lines", 2) or 2
        use_regex = getattr(params, "use_regex", False) or False
        return await code_search(
            pattern=pattern,
            cwd=cwd,
            file_pattern=file_pattern,
            max_results=max_results,
            context_lines=context_lines,
            use_regex=use_regex,
        )

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
    workspace: object,
    history_tracker: object,
    history_store: object,
    data_dir: str,
    ready_since: float,
    get_ollama_status: callable,
    get_worker_count: callable,
    db_path: str | None = None,
    smtp_config: dict | None = None,
) -> None:
    """Register all 15 tool handlers with the broker.

    Args:
        broker: Tool broker to register handlers with.
        db_client: DBProxyClient for core handlers (IPC-based).
        fs_worker: FS Worker manager for file operations.
        net_worker: Net Worker manager for network operations.
        workspace: Mutable workspace dirs container.
        history_tracker: Workspace history tracker.
        history_store: Workspace history store.
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

    broker.set_handler(
        "set_workspace",
        _make_set_workspace_handler(workspace),
    )

    broker.set_handler(
        "workspace_history",
        _make_workspace_history_handler(history_store),
    )
    broker.set_handler(
        "workspace_note",
        _make_workspace_note_handler(history_tracker, history_store),
    )

    broker.set_handler(
        "execute_command",
        _make_execute_command_handler(),
    )
    broker.set_handler(
        "execute_command_stream",
        _make_execute_command_stream_handler(),
    )

    # Git tools
    broker.set_handler("git_status", _make_git_status_handler())
    broker.set_handler("git_diff", _make_git_diff_handler())
    broker.set_handler("git_log", _make_git_log_handler())
    broker.set_handler("git_commit", _make_git_commit_handler())
    broker.set_handler("git_push", _make_git_push_handler())

    # Code search
    broker.set_handler("code_search", _make_code_search_handler())

    logger.info("All 22 tool handlers registered")


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
        "set_workspace",
        "workspace_history",
        "workspace_note",
        "execute_command",
        "execute_command_stream",
        "git_status",
        "git_diff",
        "git_log",
        "git_commit",
        "git_push",
        "code_search",
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
