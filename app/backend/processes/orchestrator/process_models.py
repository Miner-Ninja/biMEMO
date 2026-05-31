"""Data models for the orchestrator process lifecycle.

Defines the service containers and protocol types used during
orchestrator startup, runtime, and shutdown. These are shared
between process.py and its helper modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from backend.ipc.ipc_channel import IPCSocketClient
    from backend.llm.capabilities_registry import CapabilitiesRegistry

    from .client import DBProxyClient
    from .ipc_server import OrchestratorIPCServer


class DialogRecorderQueueProtocol(Protocol):
    """Protocol for the dialog recorder queue used by the scheduler bridge."""

    def put(self, item: object) -> None:
        """Enqueue a record item."""


@dataclass(frozen=True)
class RuntimeServices:
    """Prepared services and clients used by the orchestrator process runtime.

    Attributes:
        broker: Tool broker for execution.
        fs_worker: Filesystem worker process.
        net_worker: Network worker process.
        orch: Gateway orchestrator instance.
        llm_call: Async callable for LLM completion.
        llm_config: Active LLM configuration dict.
        active_service_type: LLM service type name.
        llm_client: Active raw LLM client instance.
        stt_client: Optional speech-to-text client.
        tts_client: Optional text-to-speech client.
        memory_event_bus: In-process memory event bus.
        bg_tasks: Started background tasks that depend on the active LLM.
        capabilities_registry: Process-local cache of resolved model
            capabilities (context window per provider/model).
    """

    broker: object
    fs_worker: object
    net_worker: object
    history_tracker: object
    history_store: object
    orch: object
    scheduler_orch: object | None
    llm_call: Callable | None
    llm_config: dict[str, object] | None
    active_service_type: str
    llm_client: object | None
    stt_client: object | None
    tts_client: object | None
    memory_event_bus: object
    bg_tasks: list[asyncio.Task]
    capabilities_registry: CapabilitiesRegistry


@dataclass(frozen=True)
class RuntimeStartupState:
    """Started runtime pieces needed by the main process loop.

    Attributes:
        db_ipc: Raw IPC socket client to DB Proxy.
        db_client: Typed DB proxy client.
        services: Assembled runtime services.
        ipc_server: Running IPC server for FastAPI.
        event_forwarder_task: Background task that forwards memory events.
    """

    db_ipc: IPCSocketClient
    db_client: DBProxyClient
    services: RuntimeServices
    ipc_server: OrchestratorIPCServer
    event_forwarder_task: asyncio.Task | None
