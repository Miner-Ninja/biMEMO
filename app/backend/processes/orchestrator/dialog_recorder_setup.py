"""Dialog recorder pipeline setup for the orchestrator process.

Configures the dialog recorder, summary distiller, and re-embed background
loops. Builds providers for different LLM backends, probes vector embedding
support, and wires scheduling hooks into the gateway orchestrator.
"""

import logging
from typing import TYPE_CHECKING, Protocol

import httpx

from backend.ipc.ipc_channel import IPCChannelError


if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

    from .client import DBProxyClient


logger = logging.getLogger(__name__)

RECOVERABLE_EMBEDDING_ERRORS = (
    RuntimeError,
    ValueError,
    IPCChannelError,
    httpx.HTTPError,
)
CUSTOM_API_PREFIX = "/v1"
S_ZONE_COMPRESSION_TEMPERATURE = 0.1


def build_s_zone_compression_llm_call(
    dialog_recorder_provider: object | None,
) -> Callable[[list[dict[str, str]], int], Awaitable[object]] | None:
    """Build the S-zone compression callable from the dialog recorder provider."""
    if dialog_recorder_provider is None:
        return None

    model = getattr(dialog_recorder_provider, "dialog_recorder_model", None)
    if not model:
        return None

    async def compression_llm_call(
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> object:
        """Run one S-zone compression call on the dialog recorder model."""
        from backend.llm.ollama.models import ChatMessage

        chat_messages = [
            ChatMessage(role=message["role"], content=message["content"])
            for message in messages
        ]
        return await dialog_recorder_provider.chat(
            chat_messages,
            model=model,
            max_tokens=max_tokens,
            temperature=S_ZONE_COMPRESSION_TEMPERATURE,
            enable_thinking=False,
        )

    return compression_llm_call


class _DialogRecorderSchedulerProtocol(Protocol):
    """Protocol for the dialog recorder scheduler used by the queue bridge."""

    def signal_primary_done(self) -> None:
        """Notify the scheduler that a primary item was queued."""


def _select_dialog_recorder_scheduling_mode(
    base_url: str,
    dialog_recorder_base_url: str,
) -> tuple[str, float]:
    """Always allow dialog recorder work to run in parallel with chat dialog steps."""
    del base_url
    del dialog_recorder_base_url
    return "parallel", 0.0


async def _setup_dialog_recorder_pipeline(
    orchestrator: object,
    db_client: DBProxyClient,
    llm_config: dict,
    service_type: str,
    capabilities_registry: object,
    event_bus: object | None = None,
) -> tuple[list[asyncio.Task], Callable[[str], Awaitable[list[float]]] | None]:
    """Wire dialog recorder, distiller, and re-embed background loops.

    Args:
        orchestrator: AgentOrchestrator instance.
        db_client: Connected DBProxyClient.
        llm_config: LLM credentials dict.
        service_type: Active provider type.
        event_bus: Optional memory event bus for live debug streaming.
        capabilities_registry: Process-local capabilities cache injected
            into the dialog recorder provider so its background chat
            calls can resolve their own model context window.

    Returns:
        (tasks, embed_fn) where tasks is a list of asyncio.Task
        (dialog_recorder, distiller, reembed) and embed_fn may be None.
    """
    from backend.llm.custom_provider.provider import CustomLLMProvider
    from backend.llm.openrouter.provider import OpenRouterProvider
    from backend.memory.runtime.rate_limiter import (
        DialogRecorderQueue,
        RecordRateLimiter,
    )
    from backend.memory.runtime.scheduler import (
        DialogRecorderScheduler,
        SchedulingMode,
        run_dialog_recorder_loop,
        run_graph_extractor_loop,
        run_pruning_loop,
        run_reembed_loop,
    )

    dialog_recorder_provider = _build_dialog_recorder_provider(
        llm_config=llm_config,
        service_type=service_type,
        custom_provider_cls=CustomLLMProvider,
        openrouter_provider_cls=OpenRouterProvider,
        capabilities_registry=capabilities_registry,
    )

    tasks: list[asyncio.Task] = []

    if dialog_recorder_provider is None:
        return tasks, None

    from backend.agent.memory_recall_classifier import (
        classify_requires_memory_recall,
    )
    from backend.agent.web_search_classifier import (
        classify_requires_web_search,
    )

    async def _web_search_classifier(message: str) -> bool:
        """Classify if a message needs forced web search."""
        return await classify_requires_web_search(
            message,
            dialog_recorder_provider,
        )

    async def _memory_recall_classifier(message: str) -> bool:
        """Classify if a message needs forced memory recall."""
        return await classify_requires_memory_recall(
            message,
            dialog_recorder_provider,
        )

    orchestrator.web_search_classifier_fn = _web_search_classifier
    orchestrator.memory_recall_classifier_fn = _memory_recall_classifier

    dialog_recorder_queue = DialogRecorderQueue()
    dialog_recorder_rate_limiter = RecordRateLimiter()
    dialog_recorder_scheduler = _build_dialog_recorder_scheduler(
        llm_config=llm_config,
        dialog_recorder_scheduler_cls=DialogRecorderScheduler,
        scheduling_mode_cls=SchedulingMode,
    )
    _apply_dialog_recorder_runtime_hooks(
        orchestrator=orchestrator,
        dialog_recorder_queue=dialog_recorder_queue,
        dialog_recorder_scheduler=dialog_recorder_scheduler,
        db_client=db_client,
    )
    await _reload_pending_queue_items(dialog_recorder_queue, db_client)
    embed_fn, vector_ready = await _prepare_dialog_recorder_vector_runtime(
        orchestrator=orchestrator,
        db_client=db_client,
        llm_config=llm_config,
        dialog_recorder_provider=dialog_recorder_provider,
    )
    _start_dialog_recorder_background_tasks(
        tasks=tasks,
        db_client=db_client,
        dialog_recorder_provider=dialog_recorder_provider,
        dialog_recorder_queue=dialog_recorder_queue,
        dialog_recorder_rate_limiter=dialog_recorder_rate_limiter,
        dialog_recorder_scheduler=dialog_recorder_scheduler,
        embed_fn=embed_fn,
        vector_ready=vector_ready,
        event_bus=event_bus,
        service_type=service_type,
        run_dialog_recorder_loop_fn=run_dialog_recorder_loop,
        run_extraction_loop_fn=run_graph_extractor_loop,
        run_pruning_loop_fn=run_pruning_loop,
        run_reembed_loop_fn=run_reembed_loop,
    )

    return tasks, embed_fn


async def _prepare_dialog_recorder_vector_runtime(
    *,
    orchestrator: object,
    db_client: DBProxyClient,
    llm_config: dict[str, object],
    dialog_recorder_provider: object,
) -> tuple[Callable[[str], Awaitable[list[float]]], bool]:
    """Build the embed callback, probe vector support, and apply context wiring."""
    from .llm_setup import _apply_runtime_context_fn

    async def _embed(text: str) -> list[float]:
        """Embed one dialog recorder text through the configured provider."""
        return await dialog_recorder_provider.embed(text)

    vector_ready = await _probe_vector_embedding_support(
        db_client=db_client,
        dialog_recorder_provider=dialog_recorder_provider,
        embedding_model=llm_config.get("embedding_model", ""),
    )
    _apply_runtime_context_fn(
        orchestrator=orchestrator,
        db_client=db_client,
        llm_config=llm_config,
        embed_fn=_embed if vector_ready else None,
        compression_llm_call=build_s_zone_compression_llm_call(
            dialog_recorder_provider,
        ),
    )
    return _embed, vector_ready


def _start_dialog_recorder_background_tasks(
    *,
    tasks: list[asyncio.Task],
    db_client: DBProxyClient,
    dialog_recorder_provider: object,
    dialog_recorder_queue: object,
    dialog_recorder_rate_limiter: object,
    dialog_recorder_scheduler: object,
    embed_fn: Callable[[str], Awaitable[list[float]]],
    vector_ready: bool,
    event_bus: object | None,
    service_type: str,
    run_dialog_recorder_loop_fn: object,
    run_extraction_loop_fn: object,
    run_pruning_loop_fn: object,
    run_reembed_loop_fn: object,
) -> None:
    """Start the dialog recorder background loops for one configured provider."""
    from .process import _start_runtime_background_task

    _start_runtime_background_task(
        tasks=tasks,
        coro=run_dialog_recorder_loop_fn(
            queue=dialog_recorder_queue,
            provider=dialog_recorder_provider,
            db_client=db_client,
            rate_limiter=dialog_recorder_rate_limiter,
            scheduler=dialog_recorder_scheduler,
            embed_fn=embed_fn if vector_ready else None,
            event_bus=event_bus,
        ),
        log_message="Dialog Recorder background loop started (provider=%s)",
        log_value=service_type,
    )

    _start_runtime_background_task(
        tasks=tasks,
        coro=run_extraction_loop_fn(
            provider=dialog_recorder_provider,
            db_client=db_client,
            scheduler=dialog_recorder_scheduler,
            event_bus=event_bus,
        ),
        log_message="Summary Distiller background loop started (provider=%s)",
        log_value=service_type,
    )

    _start_runtime_background_task(
        tasks=tasks,
        coro=run_pruning_loop_fn(db_client=db_client),
        log_message="Memory pruning loop started",
    )

    if vector_ready:
        _start_runtime_background_task(
            tasks=tasks,
            coro=run_reembed_loop_fn(
                db_client=db_client,
                embed_fn=embed_fn,
            ),
            log_message="Re-embed background loop started",
        )


def _build_dialog_recorder_provider(
    *,
    llm_config: dict[str, object],
    service_type: str,
    custom_provider_cls: object,
    openrouter_provider_cls: object,
    capabilities_registry: object,
) -> object | None:
    """Build the provider used by the dialog recorder pipeline."""
    from .process_helpers import parse_optional_int

    dialog_recorder_model = llm_config.get(
        "dialog_recorder_model", llm_config.get("model", "")
    )
    embedding_model = llm_config.get("embedding_model", "")
    if service_type == "openrouter":
        return openrouter_provider_cls(
            api_key=llm_config.get("api_key", ""),
            primary_model=llm_config.get("model", ""),
            dialog_recorder_model=dialog_recorder_model,
            base_url=llm_config.get("base_url", "https://openrouter.ai"),
            dialog_recorder_base_url=llm_config.get("dialog_recorder_base_url") or None,
            embedding_base_url=llm_config.get("embedding_base_url") or None,
            api_prefix="/api/v1",
            embedding_model=embedding_model,
        )
    if service_type == "custom":
        return custom_provider_cls(
            api_key=llm_config.get("api_key", ""),
            primary_model=llm_config.get("model", ""),
            dialog_recorder_model=dialog_recorder_model,
            base_url=llm_config.get("base_url", ""),
            dialog_recorder_base_url=llm_config.get("dialog_recorder_base_url") or None,
            embedding_base_url=llm_config.get("embedding_base_url") or None,
            api_prefix=CUSTOM_API_PREFIX,
            embedding_model=embedding_model,
        )
    if service_type == "ollama":
        from backend.llm.compute_config import ComputeConfig, ComputeMode
        from backend.llm.ollama.provider import OllamaProvider

        return OllamaProvider(
            base_url=llm_config.get("base_url", "http://ollama:11434"),
            dialog_recorder_base_url=llm_config.get("dialog_recorder_base_url") or None,
            primary_model=llm_config.get("model", ""),
            dialog_recorder_model=dialog_recorder_model,
            embedding_model=embedding_model,
            compute_config=ComputeConfig(mode=ComputeMode.GPU_ONLY, gpu_layers=0),
            num_ctx=parse_optional_int(llm_config.get("num_ctx")),
            capabilities_registry=capabilities_registry,
        )
    return None


def _build_dialog_recorder_scheduler(
    *,
    llm_config: dict[str, object],
    dialog_recorder_scheduler_cls: object,
    scheduling_mode_cls: object,
) -> object:
    """Build the dialog recorder scheduler for the active provider pair."""
    scheduling_mode_name, grace_period_seconds = (
        _select_dialog_recorder_scheduling_mode(
            llm_config.get("base_url", ""),
            llm_config.get("dialog_recorder_base_url", ""),
        )
    )
    return dialog_recorder_scheduler_cls(
        scheduling_mode_cls(scheduling_mode_name),
        idle_grace_period_seconds=grace_period_seconds,
    )


def _apply_dialog_recorder_runtime_hooks(
    *,
    orchestrator: object,
    dialog_recorder_queue: object,
    dialog_recorder_scheduler: object,
    db_client: object,
) -> None:
    """Attach dialog recorder callbacks and queue-backed persistence hooks."""
    from .process_helpers import build_queue_record_fn

    orchestrator.notify_primary_started = dialog_recorder_scheduler.signal_primary_start
    orchestrator.notify_primary_finished = dialog_recorder_scheduler.signal_primary_done
    orchestrator.save_record_fn = build_queue_record_fn(
        dialog_recorder_queue,
        db_client,
    )


async def _reload_pending_queue_items(
    dialog_recorder_queue: object,
    db_client: object,
) -> None:
    """Refill the in-memory recorder queue from persisted pending rows.

    Restores dialog steps that were queued but not yet processed when the
    orchestrator last stopped. Best-effort: a failure only logs — the
    recorder starts with an empty queue as before.
    """
    from backend.memory.runtime.rate_limiter_models import DialogRecorderQueueItem

    try:
        pending_items = await db_client.list_queued_records()
    except Exception:
        logger.warning(
            "Failed to reload pending dialog-recorder queue items",
            exc_info=True,
        )
        return
    for pending in pending_items:
        dialog_recorder_queue.put(
            DialogRecorderQueueItem(
                messages=list(pending.get("messages", [])),
                conversation_id=str(pending.get("conversation_id", "")),
                session_id=str(pending.get("session_id", "")),
                source_message_ids=list(pending.get("source_message_ids", [])),
                injection_flagged=bool(pending.get("injection_flagged", False)),
                queue_ids=(str(pending.get("queue_id", "")),),
                attempts=int(pending.get("attempts", 0)),
            )
        )
    if pending_items:
        logger.info(
            "Reloaded %d pending dialog-recorder queue items",
            len(pending_items),
        )


async def _probe_vector_embedding_support(
    *,
    db_client: DBProxyClient,
    dialog_recorder_provider: object,
    embedding_model: str,
) -> bool:
    """Probe embedding dimensions and initialize the vector table when possible."""
    if not embedding_model:
        logger.info("No embedding_model configured — vector search disabled")
        return False
    try:
        probe_result = await dialog_recorder_provider.embed("dimension probe")
        if not probe_result:
            logger.warning(
                "Embedding probe returned empty vector — vector search disabled"
            )
            return False
        probe_dim = len(probe_result)
        init_result = await db_client.init_vector_table(
            embedding_model,
            probe_dim,
        )
        logger.info(
            "Vector table initialized (model=%s, dim=%d, recreated=%s)",
            embedding_model,
            probe_dim,
            init_result.get("recreated"),
        )
        return True
    except RECOVERABLE_EMBEDDING_ERRORS:
        logger.warning(
            "Embedding probe failed — vector search disabled",
            exc_info=True,
        )
        return False


async def _start_runtime_dialog_recorder_tasks(
    *,
    orch: object,
    db_client: DBProxyClient,
    llm_config: dict[str, object] | None,
    active_service_type: str | None,
    memory_event_bus: object,
    capabilities_registry: object,
) -> list[asyncio.Task]:
    """Start dialog recorder background tasks when LLM config exists."""
    if llm_config is None or active_service_type is None:
        return []
    tasks, _embed_fn = await _setup_dialog_recorder_pipeline(
        orch,
        db_client,
        llm_config,
        active_service_type,
        event_bus=memory_event_bus,
        capabilities_registry=capabilities_registry,
    )
    del _embed_fn
    return tasks
