"""Orchestrator process lifecycle — startup, run, and shutdown.

Entry point for the orchestrator daemon process. Connects to the
database proxy, assembles all runtime services, starts the IPC
server, launches messenger bots, and blocks until SIGTERM.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import signal
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import llm_accepts_tools_override
from backend.ipc.ipc_channel import IPCChannelError, IPCSocketClient
from backend.ipc.models import (
    ConfirmRequest,
    ConfirmResponse,
    DBResult,
    ListAgentRunEventsRequest,
    ReloadLLMResponse,
    StreamEvent,
    StreamEventType,
)
from backend.llm.capabilities_registry import CapabilitiesRegistry
from backend.permissions import ActionGrantCache

from .client import DBProxyClient, DBProxyError
from .ipc_server import OrchestratorIPCServer
from .process_helpers import (
    init_stt_client,
    init_tts_client,
    resolve_user_tz,
)
from .process_models import (
    RuntimeServices,
    RuntimeStartupState,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.config import Settings


logger = logging.getLogger(__name__)


async def _forward_memory_events_to_ipc(
    *,
    memory_event_bus: object,
    ipc_server: OrchestratorIPCServer,
) -> None:
    """Forward memory debug events from the in-process bus into IPC."""
    sub_id, queue = memory_event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            try:
                await ipc_server.push_event(
                    StreamEvent(
                        request_id="memory_debug",
                        event_type=StreamEventType.MEMORY_EVENT,
                        data=event,
                    )
                )
            except IPCChannelError:
                logger.debug(
                    "Failed to forward memory event",
                    exc_info=True,
                )
    except asyncio.CancelledError:
        pass
    finally:
        memory_event_bus.unsubscribe(sub_id)


def _register_background_task(
    *,
    tasks: list[asyncio.Task],
    task: asyncio.Task,
    log_message: str,
    log_value: str | None = None,
) -> None:
    """Append one started background task and log its startup."""
    tasks.append(task)
    if log_value is None:
        logger.info(log_message)
        return
    logger.info(log_message, log_value)


def _start_runtime_background_task(
    *,
    tasks: list[asyncio.Task],
    coro: Awaitable[object],
    log_message: str,
    log_value: str | None = None,
) -> None:
    """Create, track, and log one runtime background task."""
    _register_background_task(
        tasks=tasks,
        task=asyncio.create_task(coro),
        log_message=log_message,
        log_value=log_value,
    )


async def _start_runtime_event_forwarder(
    *,
    memory_event_bus: object,
    ipc_server: OrchestratorIPCServer,
) -> asyncio.Task:
    """Start forwarding in-process memory events into IPC."""
    return asyncio.create_task(
        _forward_memory_events_to_ipc(
            memory_event_bus=memory_event_bus,
            ipc_server=ipc_server,
        )
    )


async def _connect_db(
    socket_path: str,
    secret: bytes,
) -> tuple[IPCSocketClient, DBProxyClient]:
    """Connect to DB Proxy Socket A and return IPC + typed client.

    Args:
        socket_path: Unix socket path to DB Proxy.
        secret: HMAC secret for the channel.

    Returns:
        Tuple of (ipc_client, db_client).
    """
    db_ipc = IPCSocketClient(
        socket_path=socket_path,
        secret=secret,
        type_map={"DBResult": DBResult},
    )
    await db_ipc.connect()
    return db_ipc, DBProxyClient(db_ipc)


async def _load_runtime_user_context(
    db_client: DBProxyClient,
) -> tuple[dict[str, str], object]:
    """Load the runtime user profile and resolve its timezone."""
    user_profile = await db_client.get_user_profile()
    return user_profile, resolve_user_tz(user_profile)


def _build_runtime_media_services(
    *,
    settings: Settings,
    memory_event_bus_cls: object,
) -> tuple[object | None, object | None, object]:
    """Build STT, TTS, and the in-process memory event bus."""
    return (
        init_stt_client(settings),
        init_tts_client(settings),
        memory_event_bus_cls(),
    )


def _build_runtime_ipc_server(
    *,
    frontend_socket: str,
    frontend_secret: bytes,
    orch: object,
    db_client: DBProxyClient,
    broker: object,
    get_llm_call: Callable[[], Callable | None],
    stt_client: object | None,
    bridge_service: object,
    ipc_server_ref: Callable[[], OrchestratorIPCServer],
    reload_llm_fn: Callable[[], object] | None = None,
) -> OrchestratorIPCServer:
    """Build the IPC server that exposes the runtime to FastAPI."""
    from .bridge_handler import _build_bridge_inbound_handler
    from .chat_handler import _build_orchestrator_request_handler
    from .factories import make_mcp_memory_remember_fn, make_save_fn

    return OrchestratorIPCServer(
        socket_path=frontend_socket,
        secret=frontend_secret,
        orchestrator_fn=_build_orchestrator_request_handler(
            orch=orch,
            ipc_server_ref=ipc_server_ref,
            db_client=db_client,
            broker=broker,
            get_llm_call=get_llm_call,
            stt_client=stt_client,
        ),
        bridge_inbound_fn=_build_bridge_inbound_handler(
            bridge_service=bridge_service,
        ),
        confirm_fn=_build_confirm_handler(
            orch=orch,
            db_client=db_client,
            broker=broker,
            get_llm_call=get_llm_call,
            ipc_server_ref=ipc_server_ref,
        ),
        save_error_response_fn=make_save_fn(db_client),
        reload_llm_fn=reload_llm_fn,
        list_run_events_fn=_build_list_run_events_handler(db_client),
        mcp_memory_remember_fn=make_mcp_memory_remember_fn(db_client),
    )


def _build_list_run_events_handler(
    db_client: DBProxyClient,
) -> Callable[[ListAgentRunEventsRequest], Awaitable[list[dict[str, object]]]]:
    """Build the handler used by FastAPI to replay runtime journal events."""

    async def list_run_events(
        request: ListAgentRunEventsRequest,
    ) -> list[dict[str, object]]:
        """Return runtime events for one reconnect request."""
        return await db_client.list_agent_run_events(
            run_id=request.run_id or None,
            conversation_id=request.conversation_id or None,
            after_sequence=request.after_sequence,
            limit=request.limit,
        )

    return list_run_events


def _build_confirm_handler(
    *,
    orch: object,
    db_client: DBProxyClient,
    broker: object,
    get_llm_call: Callable[[], Callable | None],
    ipc_server_ref: Callable[[], OrchestratorIPCServer],
) -> Callable[[ConfirmRequest], Awaitable[ConfirmResponse]]:
    """Build the confirmation handler used by FastAPI IPC."""

    async def confirm(request: ConfirmRequest) -> ConfirmResponse:
        stored_action = await db_client.get_pending_action(request.action_id)
        channel = str((stored_action or {}).get("channel", ""))
        accepted = False
        if request.decision == "approve":
            action = orch.confirmation_mgr.approve(request.action_id, channel)
            accepted = action is not None
        elif request.decision == "deny":
            accepted = orch.confirmation_mgr.deny(request.action_id)

        if stored_action is not None and accepted:
            if request.decision == "approve":
                await _push_confirmation_event(
                    ipc_server=ipc_server_ref(),
                    request_id=request.request_id,
                    action_id=request.action_id,
                    status="approved",
                )
            decision_result = await db_client.decide_pending_action(
                action_id=request.action_id,
                decision=request.decision,
                channel=channel,
            )
            accepted = bool(decision_result.get("accepted"))
            if accepted and request.decision == "approve":
                await _push_confirmation_event(
                    ipc_server=ipc_server_ref(),
                    request_id=request.request_id,
                    action_id=request.action_id,
                    status="resuming",
                )
                execution = await _execute_confirmed_action(
                    broker=broker,
                    db_client=db_client,
                    stored_action=stored_action,
                    llm_call=get_llm_call(),
                )
                await _push_confirmation_event(
                    ipc_server=ipc_server_ref(),
                    request_id=request.request_id,
                    action_id=request.action_id,
                    status=execution["status"],
                    message_id=execution["message_id"],
                )
                return ConfirmResponse(
                    request_id=request.request_id,
                    action_id=request.action_id,
                    accepted=True,
                    status=execution["status"],
                    message_id=execution["message_id"],
                    result_summary=execution["result_summary"],
                )
            if accepted and request.decision == "deny":
                message_id = await _save_denied_action_message(
                    db_client=db_client,
                    stored_action=stored_action,
                )
                await _push_confirmation_event(
                    ipc_server=ipc_server_ref(),
                    request_id=request.request_id,
                    action_id=request.action_id,
                    status="denied",
                    message_id=message_id,
                )
                return ConfirmResponse(
                    request_id=request.request_id,
                    action_id=request.action_id,
                    accepted=True,
                    status="denied",
                    message_id=message_id,
                    result_summary="The requested action was not allowed.",
                )

        return ConfirmResponse(
            request_id=request.request_id,
            action_id=request.action_id,
            accepted=accepted,
            status="denied" if accepted and request.decision == "deny" else "",
        )

    return confirm


async def _push_confirmation_event(
    *,
    ipc_server: OrchestratorIPCServer,
    request_id: str,
    action_id: str,
    status: str,
    message_id: str = "",
) -> None:
    """Push one confirmation lifecycle event to connected stream clients."""
    await ipc_server.push_event(
        StreamEvent(
            request_id=request_id,
            event_type=StreamEventType.CONFIRMATION,
            data={
                "action_id": action_id,
                "status": status,
                "message_id": message_id,
            },
        )
    )


async def _execute_confirmed_action(
    *,
    broker: object,
    db_client: DBProxyClient,
    stored_action: dict[str, object],
    llm_call: Callable | None = None,
) -> dict[str, str]:
    """Execute one approved action and persist its execution outcome."""
    action_id = str(stored_action.get("id", ""))
    run_id = str(stored_action.get("run_id", ""))
    run = await db_client.get_agent_run(run_id) if run_id else None
    context = {
        "conversation_id": str((run or {}).get("conversation_id", "")),
        "session_id": str((run or {}).get("session_id", "")),
        "channel": str(stored_action.get("channel", "")),
        "agent_run_id": run_id,
    }
    resume_context = await _load_resume_context(
        db_client=db_client,
        stored_action=stored_action,
        llm_call=llm_call,
    )
    if resume_context == "missing":
        result_summary = (
            "The approved action could not continue because its runtime "
            "resume snapshot is no longer available."
        )
        await db_client.complete_pending_action(
            action_id=action_id,
            status="failed",
            error_message=result_summary,
        )
        message_id = await _save_confirmed_action_message(
            db_client=db_client,
            run=run,
            stored_action=stored_action,
            status="failed",
            result_summary=result_summary,
        )
        return {
            "status": "failed",
            "message_id": message_id,
            "result_summary": result_summary[:500],
        }
    broker.set_context(context)
    try:
        tool_result = await broker.execute(
            str(stored_action.get("tool_name", "")),
            dict(stored_action.get("parameters", {})),
        )
    except Exception as error:
        logger.error("Confirmed action execution failed: %s", error, exc_info=True)
        result_summary = str(error)
        await db_client.complete_pending_action(
            action_id=action_id,
            status="failed",
            error_message=result_summary,
        )
        message_id = await _save_confirmed_action_message(
            db_client=db_client,
            run=run,
            stored_action=stored_action,
            status="failed",
            result_summary=result_summary,
        )
        return {
            "status": "failed",
            "message_id": message_id,
            "result_summary": result_summary[:500],
        }
    else:
        result_summary = str(getattr(tool_result, "result", ""))
        await db_client.complete_pending_action(
            action_id=action_id,
            status="completed",
            result={
                "success": bool(getattr(tool_result, "success", False)),
                "result": result_summary,
                "tool_name": str(getattr(tool_result, "tool_name", "")),
                "zone": str(getattr(getattr(tool_result, "zone", ""), "value", "")),
            },
        )
        final_answer = await _resume_llm_after_confirmed_action(
            llm_call=llm_call,
            resume_context=resume_context if isinstance(resume_context, dict) else None,
            tool_result_text=result_summary,
        )
        message_id = await _save_confirmed_action_message(
            db_client=db_client,
            run=run,
            stored_action=stored_action,
            status="completed",
            result_summary=final_answer or result_summary,
        )
        return {
            "status": "completed",
            "message_id": message_id,
            "result_summary": (final_answer or result_summary)[:500],
        }
    finally:
        broker.clear_context()


async def _resume_llm_after_confirmed_action(
    *,
    llm_call: Callable | None,
    resume_context: dict[str, object] | None,
    tool_result_text: str,
) -> str:
    """Ask the LLM for a final answer after an approved tool result."""
    if llm_call is None:
        return ""
    if resume_context is None:
        return ""
    messages = resume_context.get("messages")
    assistant_message = resume_context.get("assistant_message")
    tool_call_id = str(resume_context.get("tool_call_id", ""))
    if not isinstance(messages, list) or not isinstance(assistant_message, dict):
        return ""
    if not tool_call_id:
        return ""
    resume_messages = [
        *messages,
        assistant_message,
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": tool_result_text,
        },
    ]
    if llm_accepts_tools_override(llm_call):
        response = await llm_call(resume_messages, tools_override=[])
    else:
        response = await llm_call(resume_messages)
    structured = getattr(response, "structured_content", None)
    if structured is not None and getattr(structured, "answer", ""):
        return str(structured.answer)
    return str(getattr(response, "text", "")).strip()


async def _load_resume_context(
    *,
    db_client: DBProxyClient,
    stored_action: dict[str, object],
    llm_call: Callable | None,
) -> dict[str, object] | str | None:
    """Load continuation state or report a missing runtime snapshot."""
    if llm_call is None:
        return None
    preview = stored_action.get("preview")
    if not isinstance(preview, dict):
        return None
    snapshot_id = str(preview.get("resume_snapshot_id", ""))
    if snapshot_id:
        snapshot = await db_client.get_resume_snapshot(
            action_id=str(stored_action.get("id", "")),
            snapshot_id=snapshot_id,
        )
        snapshot_payload = (
            snapshot.get("snapshot") if isinstance(snapshot, dict) else None
        )
        return snapshot_payload if isinstance(snapshot_payload, dict) else "missing"
    if "messages" in preview or "assistant_message" in preview:
        # Backward-compatible fallback for actions created before resume_snapshots.
        return preview
    return None


async def _save_confirmed_action_message(
    *,
    db_client: DBProxyClient,
    run: dict[str, object] | None,
    stored_action: dict[str, object],
    status: str,
    result_summary: str,
) -> str:
    """Persist a concise assistant message for an approved action result."""
    conversation_id = str((run or {}).get("conversation_id", ""))
    if not conversation_id:
        return ""
    tool_name = str(stored_action.get("tool_name", ""))
    visible_status = "completed" if status == "completed" else "failed"
    content = (
        f"Approved action `{tool_name}` {visible_status}.\n\n{result_summary[:2000]}"
    ).strip()
    meta = json.dumps(
        {
            "source": "confirmed_action",
            "pending_action_id": str(stored_action.get("id", "")),
            "run_id": str(stored_action.get("run_id", "")),
            "tool_name": tool_name,
            "status": status,
        }
    )
    return await db_client.save_message(
        conversation_id,
        "assistant",
        content,
        channel=str(stored_action.get("channel", "")),
        meta=meta,
    )


async def _save_denied_action_message(
    *,
    db_client: DBProxyClient,
    stored_action: dict[str, object],
) -> str:
    """Persist a visible assistant message for a denied action."""
    run_id = str(stored_action.get("run_id", ""))
    run = await db_client.get_agent_run(run_id) if run_id else None
    conversation_id = str((run or {}).get("conversation_id", ""))
    if not conversation_id:
        return ""
    tool_name = str(stored_action.get("tool_name", ""))
    content = f"The requested action `{tool_name}` was not allowed."
    meta = json.dumps(
        {
            "source": "confirmed_action",
            "pending_action_id": str(stored_action.get("id", "")),
            "run_id": run_id,
            "tool_name": tool_name,
            "status": "denied",
        }
    )
    return await db_client.save_message(
        conversation_id,
        "assistant",
        content,
        channel=str(stored_action.get("channel", "")),
        meta=meta,
    )


async def _wait_for_sigterm() -> None:
    """Wait until the process receives SIGTERM."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    await stop_event.wait()


async def _cancel_background_tasks(
    bg_tasks: list[asyncio.Task],
) -> None:
    """Cancel and await all background tasks started by the runtime."""
    for bg_task in bg_tasks:
        if not bg_task.done():
            bg_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bg_task


_reload_lock = asyncio.Lock()


async def _close_llm_client(llm_client: object | None) -> None:
    """Close one raw LLM client when it exposes a close method."""
    if llm_client is None or not hasattr(llm_client, "close"):
        return
    close_result = llm_client.close()
    if inspect.isawaitable(close_result):
        await close_result


async def _restart_scheduler_task(
    *,
    services: RuntimeServices,
    db_client: DBProxyClient,
    llm_call: Callable | None,
    bridge_service: object,
    ipc_server: OrchestratorIPCServer,
) -> None:
    """Restart the scheduler loop for the active LLM when available."""
    if services.scheduler_orch is None or llm_call is None:
        return

    from backend.scheduler.executor import SchedulerServices
    from backend.scheduler.loop import run_scheduler_loop

    scheduler_services = SchedulerServices(
        db_client=db_client,
        orchestrator=services.scheduler_orch,
        llm_call=llm_call,
        broker=services.broker,
        sse_publish_fn=ipc_server.push_event,
        bridge_service=bridge_service,
    )
    scheduler_task = asyncio.create_task(run_scheduler_loop(scheduler_services))
    services.bg_tasks.append(scheduler_task)
    logger.info("Scheduler loop registered as background task")


async def _reload_llm_subsystem(
    *,
    services: RuntimeServices,
    db_client: DBProxyClient,
    settings: Settings,
    bridge_service: object,
    ipc_server: OrchestratorIPCServer,
) -> ReloadLLMResponse:
    """Reload the active LLM provider from fresh database configuration."""
    from .dialog_recorder_setup import _start_runtime_dialog_recorder_tasks
    from .llm_setup import _apply_llm_context_settings, _init_llm

    if _reload_lock.locked():
        return ReloadLLMResponse(
            request_id="",
            success=False,
            error="reload already in progress",
        )

    async with _reload_lock:
        await _cancel_background_tasks(services.bg_tasks)
        services.bg_tasks.clear()
        services.capabilities_registry.clear()
        try:
            await _close_llm_client(services.llm_client)
        except Exception:
            logger.warning("Failed to close old LLM client", exc_info=True)

        object.__setattr__(services, "llm_call", None)
        object.__setattr__(services, "llm_client", None)
        object.__setattr__(services, "llm_config", None)
        object.__setattr__(services, "active_service_type", "")

        try:
            llm_call, llm_config, active_service_type, llm_client = await _init_llm(
                db_client,
                settings,
                broker=services.broker,
                capabilities_registry=services.capabilities_registry,
            )
        except Exception as error:
            logger.error("LLM rebuild failed: %s", error, exc_info=True)
            return ReloadLLMResponse(
                request_id="",
                success=False,
                error=str(error),
            )

        object.__setattr__(services, "llm_call", llm_call)
        object.__setattr__(services, "llm_client", llm_client)
        object.__setattr__(services, "llm_config", llm_config)
        object.__setattr__(services, "active_service_type", active_service_type or "")

        if llm_config is not None and llm_client is not None:
            _apply_llm_context_settings(
                orch=services.orch,
                db_client=db_client,
                llm_config=llm_config,
                user_tz=None,
                capabilities_registry=services.capabilities_registry,
                llm_client=llm_client,
            )
            if services.scheduler_orch is not None:
                services.scheduler_orch.model_name = llm_config.get("model", "")

            dialog_recorder_tasks = await _start_runtime_dialog_recorder_tasks(
                orch=services.orch,
                db_client=db_client,
                llm_config=llm_config,
                active_service_type=active_service_type,
                memory_event_bus=services.memory_event_bus,
                capabilities_registry=services.capabilities_registry,
            )
            services.bg_tasks.extend(dialog_recorder_tasks)

        await _restart_scheduler_task(
            services=services,
            db_client=db_client,
            llm_call=llm_call,
            bridge_service=bridge_service,
            ipc_server=ipc_server,
        )

        return ReloadLLMResponse(
            request_id="",
            success=True,
            active_provider=active_service_type,
            model=(
                str(llm_config.get("model", "")) if llm_config is not None else None
            ),
        )


def _build_reload_fn(
    *,
    services: RuntimeServices,
    db_client: DBProxyClient,
    settings: Settings,
    bridge_service: object,
    ipc_server: OrchestratorIPCServer,
) -> Callable[[], object]:
    """Build the closure used by IPC to trigger an LLM reload."""

    async def reload_llm() -> ReloadLLMResponse:
        """Reload the LLM subsystem from the active saved configuration."""
        return await _reload_llm_subsystem(
            services=services,
            db_client=db_client,
            settings=settings,
            bridge_service=bridge_service,
            ipc_server=ipc_server,
        )

    return reload_llm


async def _stop_runtime_workers(
    *,
    fs_worker: object,
    net_worker: object,
) -> None:
    """Stop the in-process tool workers during runtime shutdown."""
    await fs_worker.stop()
    await net_worker.stop()


async def _shutdown_orchestrator_runtime(
    *,
    telegram_bot: object | None,
    discord_bot: object | None,
    event_forwarder_task: asyncio.Task | None,
    bg_tasks: list[asyncio.Task],
    fs_worker: object,
    net_worker: object,
    ipc_server: OrchestratorIPCServer,
    db_ipc: IPCSocketClient,
) -> None:
    """Stop bots, background tasks, workers, IPC, and DB connection."""
    from .bot_launcher import _stop_runtime_bots

    await _stop_runtime_bots(
        telegram_bot=telegram_bot,
        discord_bot=discord_bot,
    )
    if event_forwarder_task is not None and not event_forwarder_task.done():
        event_forwarder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_forwarder_task
    await _cancel_background_tasks(bg_tasks)
    await _stop_runtime_workers(
        fs_worker=fs_worker,
        net_worker=net_worker,
    )
    await ipc_server.stop()
    await db_ipc.close()


async def _prepare_runtime_services(
    *,
    db_client: DBProxyClient,
    settings: Settings,
) -> RuntimeServices:
    """Build the runtime services needed before the IPC server starts."""
    capabilities_registry = CapabilitiesRegistry()
    core = await _prepare_runtime_core_services(
        db_client=db_client,
        settings=settings,
        capabilities_registry=capabilities_registry,
    )
    support = await _prepare_runtime_support_services(
        orch=core[5],
        db_client=db_client,
        settings=settings,
        llm_config=core[8],
        active_service_type=core[9],
        capabilities_registry=capabilities_registry,
    )
    return RuntimeServices(
        broker=core[0],
        fs_worker=core[1],
        net_worker=core[2],
        history_tracker=core[3],
        history_store=core[4],
        orch=core[5],
        scheduler_orch=core[6],
        llm_call=core[7],
        llm_config=core[8],
        active_service_type=core[9],
        llm_client=core[10],
        stt_client=support[0],
        tts_client=support[1],
        memory_event_bus=support[2],
        bg_tasks=support[3],
        capabilities_registry=capabilities_registry,
    )


async def _prepare_runtime_core_services(
    *,
    db_client: DBProxyClient,
    settings: Settings,
    capabilities_registry: CapabilitiesRegistry,
) -> tuple:
    """Build the core broker, workers, orchestrator, and LLM pieces."""
    from backend.agent.concurrency import AgentConcurrency

    from .llm_setup import (
        _apply_llm_context_settings,
        _build_runtime_orchestrator,
        _init_llm,
    )
    from .tool_setup import _setup_tools

    broker, fs_worker, net_worker, history_tracker, history_store = await _setup_tools(
        db_client,
        settings,
    )
    user_profile, user_tz = await _load_runtime_user_context(db_client)
    try:
        action_grants = ActionGrantCache(await db_client.list_capability_grants())
    except DBProxyError as error:
        logger.warning("Could not load action grants: %s", error)
        action_grants = ActionGrantCache()
    orch = _build_runtime_orchestrator(
        broker=broker,
        db_client=db_client,
        user_profile=user_profile,
        user_tz=user_tz,
        gateway_concurrency_cls=AgentConcurrency,
        action_grants=action_grants,
    )
    llm_call, llm_config, active_service_type, llm_client = await _init_llm(
        db_client,
        settings,
        broker=broker,
        capabilities_registry=capabilities_registry,
    )
    _apply_llm_context_settings(
        orch=orch,
        db_client=db_client,
        llm_config=llm_config,
        user_tz=user_tz,
        capabilities_registry=capabilities_registry,
        llm_client=llm_client,
    )
    # Build a separate orchestrator for scheduled tasks
    scheduler_orch = None
    if llm_call is not None:
        scheduler_orch = _build_runtime_orchestrator(
            broker=broker,
            db_client=db_client,
            user_profile=user_profile,
            user_tz=user_tz,
            gateway_concurrency_cls=AgentConcurrency,
            action_grants=action_grants,
        )

    return (
        broker,
        fs_worker,
        net_worker,
        history_tracker,
        history_store,
        orch,
        scheduler_orch,
        llm_call,
        llm_config,
        active_service_type,
        llm_client,
    )


async def _prepare_runtime_support_services(
    *,
    orch: object,
    db_client: DBProxyClient,
    settings: Settings,
    llm_config: dict[str, object] | None,
    active_service_type: str | None,
    capabilities_registry: CapabilitiesRegistry | None = None,
) -> tuple:
    """Build media services and start dialog recorder background tasks."""
    from backend.memory.runtime.event_bus import MemoryEventBus

    from .dialog_recorder_setup import (
        _start_runtime_dialog_recorder_tasks,
    )

    stt_client, tts_client, memory_event_bus = _build_runtime_media_services(
        settings=settings,
        memory_event_bus_cls=MemoryEventBus,
    )
    bg_tasks = await _start_runtime_dialog_recorder_tasks(
        orch=orch,
        db_client=db_client,
        llm_config=llm_config,
        active_service_type=active_service_type,
        memory_event_bus=memory_event_bus,
        capabilities_registry=capabilities_registry,
    )
    return stt_client, tts_client, memory_event_bus, bg_tasks


async def _start_runtime_process(
    *,
    db_proxy_socket_a: str,
    db_proxy_secret_a: bytes,
    frontend_socket: str,
    frontend_secret: bytes,
    settings: Settings,
) -> RuntimeStartupState:
    """Start the runtime core up to the point where bots launch."""
    from .bridge_handler import (
        _build_bridge_message_processor,
        _build_bridge_service,
    )

    db_ipc, db_client = await _connect_db(
        db_proxy_socket_a,
        db_proxy_secret_a,
    )
    stuck_reset_count = await db_client.reset_stuck_processing_states()
    if stuck_reset_count > 0:
        logger.info(
            "Cleared %d stuck processing states on startup",
            stuck_reset_count,
        )
    else:
        logger.debug("No stuck processing states to clear on startup")
    services = await _prepare_runtime_services(
        db_client=db_client,
        settings=settings,
    )
    ipc_server: OrchestratorIPCServer | None = None

    def require_ipc_server() -> OrchestratorIPCServer:
        """Return the runtime IPC server after startup finishes."""
        if ipc_server is None:
            raise RuntimeError("IPC server is not initialized yet")
        return ipc_server

    def _get_llm_call() -> Callable | None:
        """Return the current LLM callable stored in runtime services."""
        return services.llm_call

    bridge_service = _build_bridge_service(
        db_client=db_client,
        inbound_message_processor=_build_bridge_message_processor(
            orch=services.orch,
            db_client=db_client,
            get_llm_call=_get_llm_call,
        ),
        settings=settings,
    )

    ipc_server = _build_runtime_ipc_server(
        frontend_socket=frontend_socket,
        frontend_secret=frontend_secret,
        orch=services.orch,
        db_client=db_client,
        broker=services.broker,
        get_llm_call=_get_llm_call,
        stt_client=services.stt_client,
        bridge_service=bridge_service,
        ipc_server_ref=require_ipc_server,
        reload_llm_fn=lambda: _build_reload_fn(
            services=services,
            db_client=db_client,
            settings=settings,
            bridge_service=bridge_service,
            ipc_server=require_ipc_server(),
        )(),
    )
    await ipc_server.start()
    logger.info("Orchestrator started (IPC: %s)", frontend_socket)

    event_forwarder_task = await _start_runtime_event_forwarder(
        memory_event_bus=services.memory_event_bus,
        ipc_server=ipc_server,
    )

    # Start the scheduler loop if LLM is configured
    await _restart_scheduler_task(
        services=services,
        db_client=db_client,
        llm_call=services.llm_call,
        bridge_service=bridge_service,
        ipc_server=ipc_server,
    )

    return RuntimeStartupState(
        db_ipc=db_ipc,
        db_client=db_client,
        services=services,
        ipc_server=ipc_server,
        event_forwarder_task=event_forwarder_task,
    )


def orchestrator_main(
    db_proxy_socket_a: str,
    db_proxy_secret_a: bytes,
    frontend_socket: str,
    frontend_secret: bytes,
) -> None:
    """Synchronous entry point for the Orchestrator process.

    Args:
        db_proxy_socket_a: Path to DB Proxy Socket A.
        db_proxy_secret_a: HMAC secret for Socket A.
        frontend_socket: Path for FastAPI-facing IPC socket.
        frontend_secret: HMAC secret for frontend channel.
    """
    from backend.config import get_settings

    from .bot_launcher import _start_runtime_messenger_bots

    settings = get_settings()

    async def run() -> None:
        """Connect to DB Proxy and start orchestrator."""
        startup = await _start_runtime_process(
            db_proxy_socket_a=db_proxy_socket_a,
            db_proxy_secret_a=db_proxy_secret_a,
            frontend_socket=frontend_socket,
            frontend_secret=frontend_secret,
            settings=settings,
        )
        telegram_bot, discord_bot = await _start_runtime_messenger_bots(
            startup=startup,
        )
        await _wait_for_sigterm()
        await _shutdown_orchestrator_runtime(
            telegram_bot=telegram_bot,
            discord_bot=discord_bot,
            event_forwarder_task=startup.event_forwarder_task,
            bg_tasks=startup.services.bg_tasks,
            fs_worker=startup.services.fs_worker,
            net_worker=startup.services.net_worker,
            ipc_server=startup.ipc_server,
            db_ipc=startup.db_ipc,
        )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(run())
    loop.close()
