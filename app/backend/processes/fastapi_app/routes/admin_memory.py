"""Admin routes for memory debug dashboard."""

# ruff: noqa: B008

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.files import FileContextManager
from backend.tools.handlers.registration import get_recall_stats

from ..deps import (
    _get_admin_client,
    _require_auth,
)
from . import files as files_routes


if TYPE_CHECKING:
    from backend.channels.jwt_auth import TokenClaims

    from ..clients import AdminDBClient, CredentialClient


memory_router = APIRouter(prefix="/admin/memory")

DEFAULT_MEMORY_LIST_LIMIT = 50
MAX_MEMORY_LIST_LIMIT = 500
FILE_CONTEXT_MANAGER = FileContextManager()


def _memory_list_response(key: str, items: object) -> dict[str, object]:
    """Wrap one memory-admin list payload under its response key."""
    return {key: items}


def _optional_filter(value: str) -> str | None:
    """Return one query filter string or None when it is empty."""
    return value or None


async def _stored_file_metadata(
    admin: AdminDBClient,
    file_id: str,
) -> tuple[dict | None, dict | None]:
    """Load stored file context and matching file metadata."""
    file_context = await admin.get_file_context(file_id)
    if file_context is None:
        return None, None
    files = await admin.list_files()
    file_metadata = next(
        (entry for entry in files if entry.get("id") == file_id),
        None,
    )
    return file_context, file_metadata


def _stored_file_user_query(file_context: dict[str, object]) -> str:
    """Extract the last user query from stored file-context usage."""
    last_usage = file_context.get("last_usage")
    if not isinstance(last_usage, dict):
        return ""
    return str(last_usage.get("user_query", ""))


def _stored_file_raw_content(
    *,
    file_id: str,
    file_metadata: dict[str, object],
    stored_context: object,
) -> str | None:
    """Load raw file text when preview is small enough and file still exists."""
    if (
        stored_context.analysis.estimated_tokens
        > FILE_CONTEXT_MANAGER.preview_token_limit
    ):
        return None
    extension = Path(str(file_metadata["original_name"])).suffix.lower()
    file_path = files_routes.FILES_DIR / f"{file_id}{extension}"
    if not file_path.exists():
        return None
    return file_path.read_text(errors="replace")


def _stored_file_prompt_blocks(
    *,
    file_id: str,
    file_metadata: dict[str, object],
    file_context: dict[str, object],
) -> list[str]:
    """Build debug prompt blocks for one stored file context."""
    stored_context = FILE_CONTEXT_MANAGER.deserialize_stored_file_context(
        file_id=file_id,
        file_name=str(file_metadata["original_name"]),
        mime_type=str(file_metadata["mime_type"]),
        size_bytes=int(file_metadata["size"]),
        payload=file_context,
    )
    if stored_context is None:
        return []
    raw_content = _stored_file_raw_content(
        file_id=file_id,
        file_metadata=file_metadata,
        stored_context=stored_context,
    )
    try:
        prompt_preview = FILE_CONTEXT_MANAGER.build_file_context_from_storage(
            stored_context=stored_context,
            user_query=_stored_file_user_query(file_context),
            content=raw_content,
        )
    except ValueError:
        return []
    return prompt_preview.prompt_blocks


@memory_router.get("/pipeline")
async def get_pipeline_status(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return memory pipeline status (counts of entries)."""
    del _claims
    counts = await admin.count_memory_entries()
    return {
        "messages": counts.get("messages", 0),
        "records": counts.get("records", 0),
        "assertions": counts.get("assertions", 0),
    }


@memory_router.get("/messages")
async def get_messages_view(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(
        default=DEFAULT_MEMORY_LIST_LIMIT,
        ge=1,
        le=MAX_MEMORY_LIST_LIMIT,
    ),
    offset: int = Query(default=0, ge=0),
    conversation_id: str = Query(default=""),
) -> dict:
    """Return raw message entries for diagnostic tooling.

    Args:
        admin: Database admin client.
        limit: Maximum number of entries to return.
        offset: Pagination offset.
        conversation_id: Optional conversation filter.

    Returns:
        Dict with raw message entries.
    """
    del _claims
    return _memory_list_response(
        "messages",
        await admin.list_messages(
            limit=limit,
            offset=offset,
            conversation_id=_optional_filter(conversation_id),
        ),
    )


@memory_router.get("/records")
async def get_observations_view(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(
        default=DEFAULT_MEMORY_LIST_LIMIT,
        ge=1,
        le=MAX_MEMORY_LIST_LIMIT,
    ),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return full record entries for diagnostic tooling.

    Args:
        admin: Database admin client.
        limit: Maximum number of entries to return.
        offset: Pagination offset.

    Returns:
        Dict with record entries.
    """
    del _claims
    return _memory_list_response(
        "records",
        await admin.list_records(limit=limit, offset=offset),
    )


@memory_router.get("/assertions")
async def get_assertions_view(
    query: str = "",
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(
        default=DEFAULT_MEMORY_LIST_LIMIT,
        ge=1,
        le=MAX_MEMORY_LIST_LIMIT,
    ),
) -> dict:
    """Return graph assertions for diagnostic tooling.

    With a query — matching entry points; without — newest assertions
    via the timeline report.
    """
    del _claims
    if query:
        hits = await admin.find_graph_entry_points(query, limit)
        return {"assertions": [h for h in hits if h.get("kind") == "assertion"]}
    timeline = await admin.get_memory_timeline(
        entry_type="assertion",
        limit=limit,
    )
    return {"assertions": timeline.get("entries", [])}


@memory_router.get("/context")
async def get_context_view(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return context assembly breakdown by tier."""
    del _claims
    return await admin.get_memory_context_view()


@memory_router.get("/quality")
async def get_quality_view(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return a quality report for the current memory graph."""
    del _claims
    return await admin.get_memory_quality_report()


@memory_router.get("/timeline")
async def get_timeline(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    entry_type: str = Query(default="", alias="type"),
    sort: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return memory timeline with optional type filter and sort order.

    Args:
        admin: Admin client used to fetch memory entries.
        entry_type: Optional timeline filter for message/record/summary.
        limit: Maximum number of entries to return.
        offset: Pagination offset.
        sort: "desc" (newest first, default) or "asc" (oldest first).
    """
    del _claims
    return await admin.get_memory_timeline(
        entry_type=entry_type,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@memory_router.get("/audit")
async def get_audit_log(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    event_type: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return audit log entries with optional event_type filter."""
    del _claims
    return _memory_list_response(
        "entries",
        await admin.list_audit_log(
            limit=limit,
            offset=offset,
            event_type=_optional_filter(event_type),
        ),
    )


@memory_router.get("/files")
async def get_file_context_index(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return uploaded files for file-context debugging."""
    del _claims
    return _memory_list_response("files", await admin.list_files())


@memory_router.get("/files/{file_id}/context")
async def get_file_context_view(
    file_id: str,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return stored file context for one uploaded file."""
    del _claims
    file_context, file_metadata = await _stored_file_metadata(admin, file_id)
    if file_context is None:
        return {"file_context": None}
    if file_metadata is None:
        return {"file_context": file_context}
    return {
        "file_context": {
            **file_context,
            "prompt_blocks": _stored_file_prompt_blocks(
                file_id=file_id,
                file_metadata=file_metadata,
                file_context=file_context,
            ),
        }
    }


@memory_router.get("/route-diagnostics")
async def get_route_diagnostics(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    final_intent: str = Query(default=""),
    mismatch_only: bool = Query(default=False),
    fallback_only: bool = Query(default=False),
) -> dict:
    """Return stored memory-route diagnostics for admin debugging.

    Args:
        admin: Database admin client.
        limit: Maximum number of rows to return.
        offset: Pagination offset.
        final_intent: Optional final route intent filter.
        mismatch_only: When true, only return rows where route changed.
        fallback_only: When true, only return rows where fallback ran.

    Returns:
        Dict with paginated diagnostics entries.
    """
    del _claims
    return _memory_list_response(
        "diagnostics",
        await admin.list_memory_route_diagnostics(
            limit=limit,
            offset=offset,
            final_intent=_optional_filter(final_intent),
            mismatch_only=mismatch_only,
            fallback_only=fallback_only,
        ),
    )


@memory_router.get("/route-diagnostics/summary")
async def get_route_diagnostics_summary(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    recent_hours: int | None = Query(default=None, ge=1, le=720),
) -> dict:
    """Return aggregated route-diagnostics metrics for admin debugging."""
    del _claims
    return await admin.get_memory_route_diagnostics_summary(
        recent_hours=recent_hours,
    )


@memory_router.get("/dialog-recorder-trace")
async def get_dialog_recorder_trace(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    decision: str = Query(default=""),
) -> dict:
    """Return recent record-processing diagnostics from diagnostics DB."""
    del _claims
    return _memory_list_response(
        "entries",
        await admin.list_record_processing_diagnostics(
            limit=limit,
            decision=_optional_filter(decision),
        ),
    )


@memory_router.get("/distiller-trace")
async def get_distiller_trace(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    decision: str = Query(default=""),
) -> dict:
    """Return recent summary-processing diagnostics from diagnostics DB."""
    del _claims
    return _memory_list_response(
        "entries",
        await admin.list_summary_processing_diagnostics(
            limit=limit,
            decision=_optional_filter(decision),
        ),
    )


async def _resolve_num_ctx(
    cred: CredentialClient | None,
) -> int:
    """Load num_ctx from LLM config via credential proxy."""
    if cred is None:
        return 0
    try:
        meta = await cred.get_llm_config_meta()
        return int(meta.get("num_ctx") or 0)
    except Exception:
        return 0


@memory_router.get("/health")
async def get_memory_health(
    request: Request,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return a unified memory subsystem health snapshot."""
    del _claims
    counts = await admin.count_memory_entries()

    from backend.memory.context_builder.models import ContextZoneBudget

    cred = getattr(request.app.state, "credential_client", None)
    num_ctx = await _resolve_num_ctx(cred)
    budget = ContextZoneBudget.from_context_window(
        num_ctx=num_ctx,
    )

    return {
        "pipeline": {
            "messages": counts.get("messages", 0),
            "records": counts.get("records", 0),
            "assertions": counts.get("assertions", 0),
        },
        "zones": {
            "num_ctx": num_ctx,
            "working_memory": {"budget": budget.working_memory},
            "recall_results": {"budget": budget.recall_results},
            "conversation_summary": {
                "budget": budget.conversation_summary,
            },
            "response_reserve": budget.response_reserve,
            "total_available": budget.total_available,
        },
        "retrieval": get_recall_stats(),
        "graph": await admin.get_graph_stats(),
    }


@memory_router.get("/graph/entities")
async def search_graph_entities(
    query: str = "",
    limit: int = 20,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Search graph entities by name or alias."""
    del _claims
    limit = max(1, min(limit, 100))
    entities = await admin.find_entities(query, limit) if query else []
    return {"entities": entities}


@memory_router.get("/graph/entities/{entity_id}")
async def get_graph_entity(
    entity_id: str,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return one entity with its assertions in both roles."""
    del _claims
    entity = await admin.get_entity(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    assertions = await admin.list_assertions_for_entity(entity_id)
    return {"entity": entity, "assertions": assertions}
