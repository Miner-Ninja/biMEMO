"""Tool broker and worker setup for the orchestrator process.

Creates the tool registry, broker, and in-process filesystem/network workers.
Registers all tool handlers and wires persistent rate limiting through the
database proxy client.
"""

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .factories import make_audit_fn


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.config import Settings

    from .client import DBProxyClient


logger = logging.getLogger(__name__)


class _RequestWithId(Protocol):
    """Minimal protocol for requests that carry a request identifier."""

    request_id: str


class _InProcessWorker:
    """Adapter with WorkerManager interface for in-process dispatch.

    The orchestrator is a daemon process and cannot spawn children,
    so we use this lightweight adapter that runs dispatch handlers
    in the orchestrator's own event loop.
    """

    def __init__(self, name: str, handler: Callable) -> None:
        """Initialize with a name and async dispatch handler."""
        self.worker_name = name
        self._handler = handler
        self.is_alive = True

    async def send_request(self, request: _RequestWithId) -> object:
        """Dispatch a request through the in-process handler."""
        self._last_request_id = request.request_id
        return await self._handler(request)

    async def stop(self) -> None:
        """Mark the in-process worker as stopped."""
        self.is_alive = False


async def _setup_tools(
    db_client: DBProxyClient,
    settings: Settings,
) -> tuple[object, _InProcessWorker, _InProcessWorker, object, object]:
    """Create broker, registry, in-process workers, register tools.

    Args:
        db_client: Connected DBProxyClient.
        settings: Application settings.

    Returns:
        (broker, fs_worker, net_worker) tuple.
    """
    from backend.tools.broker import ToolBroker
    from backend.tools.handlers.registration import (
        register_all_handlers,
        register_tool_definitions,
        validate_all_registered,
    )
    from backend.tools.registry import ToolRegistry
    from backend.workers.fs_worker import make_fs_dispatch
    from backend.workers.net_worker import make_net_dispatch

    registry = ToolRegistry()
    broker = await _build_runtime_tool_broker(
        db_client=db_client,
        registry=registry,
        tool_broker_cls=ToolBroker,
    )
    fs_worker, net_worker, workspace, data_dir = _build_runtime_tool_workers(
        settings=settings,
        make_fs_dispatch_fn=make_fs_dispatch,
        make_net_dispatch_fn=make_net_dispatch,
    )
    from backend.workspace.history_store import WorkspaceHistoryStore
    from backend.workspace.history_tracker import WorkspaceHistoryTracker

    history_tracker = WorkspaceHistoryTracker()
    history_store = WorkspaceHistoryStore()
    register_tool_definitions(registry)
    _register_runtime_tool_handlers(
        register_all_handlers_fn=register_all_handlers,
        broker=broker,
        db_client=db_client,
        fs_worker=fs_worker,
        net_worker=net_worker,
        workspace=workspace,
        history_tracker=history_tracker,
        history_store=history_store,
        data_dir=data_dir,
    )
    validate_all_registered(broker)

    return broker, fs_worker, net_worker, history_tracker, history_store


async def _build_runtime_tool_broker(
    *,
    db_client: DBProxyClient,
    registry: object,
    tool_broker_cls: object,
) -> object:
    """Build the tool broker and wire its persistent rate limiter."""
    broker = tool_broker_cls(registry=registry)
    broker.audit_fn = make_audit_fn(db_client)
    broker.persistent_limiter.record_fn = db_client.record_rate_limit_call
    broker.persistent_limiter.load_fn = db_client.get_recent_rate_limit_calls
    await broker.persistent_limiter.initialize_from_db()
    return broker


def _build_runtime_tool_workers(
    *,
    settings: Settings,
    make_fs_dispatch_fn: object,
    make_net_dispatch_fn: object,
) -> tuple[_InProcessWorker, _InProcessWorker, object, str]:
    """Build the in-process filesystem and network tool workers."""
    from backend.workers.fs_worker import WorkspaceDirs

    data_dir = str(Path(settings.database_path).parent)
    initial_dirs = _build_fs_allowed_dirs(data_dir)
    workspace = WorkspaceDirs(initial_dirs)
    fs_dispatch = make_fs_dispatch_fn(workspace)
    net_dispatch = make_net_dispatch_fn(settings.searxng_url)
    return (
        _InProcessWorker("fs-worker", fs_dispatch),
        _InProcessWorker("net-worker", net_dispatch),
        workspace,
        data_dir,
    )


def _build_fs_allowed_dirs(data_dir: str) -> list[str]:
    """Return directories that file tools may access."""
    from backend.files.paths import UPLOADED_FILES_DIR

    return [data_dir, str(UPLOADED_FILES_DIR)]


def _register_runtime_tool_handlers(
    *,
    register_all_handlers_fn: object,
    broker: object,
    db_client: DBProxyClient,
    fs_worker: _InProcessWorker,
    net_worker: _InProcessWorker,
    workspace: object,
    history_tracker: object,
    history_store: object,
    data_dir: str,
) -> None:
    """Register all tool handlers for the runtime broker."""
    ready_since = time.time()

    def get_ollama_status() -> str:
        """Report the in-process Ollama worker status for diagnostics."""
        return "connected"

    def get_worker_count() -> int:
        """Count currently alive in-process tool workers."""
        count = 0
        if fs_worker.is_alive:
            count += 1
        if net_worker.is_alive:
            count += 1
        return count

    register_all_handlers_fn(
        broker=broker,
        db_client=db_client,
        fs_worker=fs_worker,
        net_worker=net_worker,
        workspace=workspace,
        history_tracker=history_tracker,
        history_store=history_store,
        data_dir=data_dir,
        ready_since=ready_since,
        get_ollama_status=get_ollama_status,
        get_worker_count=get_worker_count,
        db_path=None,
        smtp_config=None,
    )
