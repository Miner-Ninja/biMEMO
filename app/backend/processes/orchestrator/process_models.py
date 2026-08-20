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

    from backend.agent.agent_loop import AgentOrchestrator
    from backend.ipc.ipc_channel import IPCSocketClient
    from backend.llm.capabilities_registry import CapabilitiesRegistry
    from backend.llm.custom_provider.client import CustomLLMClient
    from backend.llm.ollama.client import OllamaClient
    from backend.llm.openrouter.client import OpenRouterClient
    from backend.llm.stt_client import STTClient
    from backend.llm.tts_client import TTSClient
    from backend.memory.runtime.event_bus import MemoryEventBus
    from backend.tools.broker import ToolBroker
    from backend.workspace.history_store import WorkspaceHistoryStore
    from backend.workspace.history_tracker import WorkspaceHistoryTracker

    from .client import DBProxyClient
    from .ipc_server import OrchestratorIPCServer
    from .tool_setup import _InProcessWorker


class DialogRecorderQueueProtocol(Protocol):
    """Protocol for the dialog recorder queue used by the scheduler bridge."""

    def put(self, item: object) -> None:
        """Enqueue a record item."""


@dataclass(frozen=True)
class RuntimeCoreServices:
    """Core services built before support services.

    Attributes:
        broker: Tool broker for execution.
        fs_worker: In-process filesystem worker.
        net_worker: In-process network worker.
        history_tracker: Workspace history tracker.
        history_store: Workspace history store.
        orch: Gateway orchestrator instance.
        scheduler_orch: Separate orchestrator for scheduled tasks; None
            when no LLM provider is configured.
        llm_call: Async callable for LLM completion; None in degraded mode.
        llm_config: Active LLM configuration dict; None in degraded mode.
        active_service_type: LLM service type name; None in degraded mode.
        llm_client: Active raw LLM client instance; None in degraded mode.
    """

    broker: ToolBroker
    fs_worker: _InProcessWorker
    net_worker: _InProcessWorker
    history_tracker: WorkspaceHistoryTracker
    history_store: WorkspaceHistoryStore
    orch: AgentOrchestrator
    scheduler_orch: AgentOrchestrator | None
    llm_call: Callable | None
    llm_config: dict[str, object] | None
    active_service_type: str | None
    llm_client: OllamaClient | OpenRouterClient | CustomLLMClient | None


@dataclass(frozen=True)
class RuntimeSupportServices:
    """Media services and dialog recorder tasks built after the core.

    Attributes:
        stt_client: Optional speech-to-text client.
        tts_client: Optional text-to-speech client.
        memory_event_bus: In-process memory event bus.
        bg_tasks: Started background tasks that depend on the active LLM.
    """

    stt_client: STTClient | None
    tts_client: TTSClient | None
    memory_event_bus: MemoryEventBus
    bg_tasks: list[asyncio.Task]


@dataclass(frozen=True)
class RuntimeServices:
    """Prepared services and clients used by the orchestrator process runtime.

    Attributes:
        broker: Tool broker for execution.
        fs_worker: In-process filesystem worker.
        net_worker: In-process network worker.
        history_tracker: Workspace history tracker.
        history_store: Workspace history store.
        orch: Gateway orchestrator instance.
        scheduler_orch: Separate orchestrator for scheduled tasks; None
            when no LLM provider is configured.
        llm_call: Async callable for LLM completion; None in degraded mode.
        llm_config: Active LLM configuration dict; None in degraded mode.
        active_service_type: LLM service type name; None in degraded mode.
        llm_client: Active raw LLM client instance; None in degraded mode.
        stt_client: Optional speech-to-text client.
        tts_client: Optional text-to-speech client.
        memory_event_bus: In-process memory event bus.
        bg_tasks: Started background tasks that depend on the active LLM.
        capabilities_registry: Process-local cache of resolved model
            capabilities (context window per provider/model).
    """

    broker: ToolBroker
    fs_worker: _InProcessWorker
    net_worker: _InProcessWorker
    history_tracker: WorkspaceHistoryTracker
    history_store: WorkspaceHistoryStore
    orch: AgentOrchestrator
    scheduler_orch: AgentOrchestrator | None
    llm_call: Callable | None
    llm_config: dict[str, object] | None
    active_service_type: str | None
    llm_client: OllamaClient | OpenRouterClient | CustomLLMClient | None
    stt_client: STTClient | None
    tts_client: TTSClient | None
    memory_event_bus: MemoryEventBus
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
