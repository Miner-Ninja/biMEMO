"""IPC server for the Orchestrator ↔ FastAPI channel."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from typing import TYPE_CHECKING

from backend.chat_bridge.service_models import ChatBridgeSyncSkippedError
from backend.common import await_maybe
from backend.ipc.errors import user_safe_error_text
from backend.ipc.ipc_channel import IPCSocketServer
from backend.ipc.models import (
    BridgeInboundSyncRequest,
    BridgeInboundSyncResponse,
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    ConfirmResponse,
    ListAgentRunEventsRequest,
    ListAgentRunEventsResponse,
    McpAskRequest,
    McpAskResponse,
    McpMemoryRememberRequest,
    McpMemoryRememberResponse,
    MemoryInvalidateRequest,
    MemoryInvalidateResponse,
    ReloadLLMRequest,
    ReloadLLMResponse,
    ScheduledToolAuthorizationRequest,
    ScheduledToolAuthorizationResponse,
    StopRequest,
    StopResponse,
    StreamEvent,
    StructuredAssistantContent,
    ToolCatalogRequest,
    ToolCatalogResponse,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

logger = logging.getLogger(__name__)

FRONTEND_TYPE_MAP = {
    "BridgeInboundSyncRequest": BridgeInboundSyncRequest,
    "ChatRequest": ChatRequest,
    "ListAgentRunEventsRequest": ListAgentRunEventsRequest,
    "McpAskRequest": McpAskRequest,
    "McpMemoryRememberRequest": McpMemoryRememberRequest,
    "MemoryInvalidateRequest": MemoryInvalidateRequest,
    "ReloadLLMRequest": ReloadLLMRequest,
    "StopRequest": StopRequest,
    "ConfirmRequest": ConfirmRequest,
    "ToolCatalogRequest": ToolCatalogRequest,
    "ScheduledToolAuthorizationRequest": ScheduledToolAuthorizationRequest,
}


class OrchestratorIPCServer:
    """IPC server that accepts commands from the FastAPI process.

    Handles ChatRequest (starts agentic loop), StopRequest (cancels),
    and pushes StreamEvent/ChatResponse back to the client.

    Attributes:
        _server: Underlying IPCSocketServer.
        _orchestrator_fn: Async callable for processing chat messages.
        _active_task: Currently running agentic loop task.
    """

    def __init__(
        self,
        socket_path: str,
        secret: bytes,
        orchestrator_fn: Callable,
        confirm_fn: Callable | None = None,
        bridge_inbound_fn: Callable | None = None,
        save_error_response_fn: Callable | None = None,
        reload_llm_fn: Callable | None = None,
        list_run_events_fn: Callable | None = None,
        mcp_memory_remember_fn: Callable | None = None,
        mcp_ask_fn: Callable | None = None,
        memory_invalidate_fn: Callable | None = None,
        tool_catalog_fn: Callable | None = None,
        scheduled_tool_authorization_fn: Callable | None = None,
    ) -> None:
        """Initialize the orchestrator IPC server.

        Args:
            socket_path: Unix socket path.
            secret: HMAC shared secret.
            orchestrator_fn: Async callable(ChatRequest) -> ChatResponse.
            confirm_fn: Optional async callable(ConfirmRequest) -> ConfirmResponse.
            bridge_inbound_fn: Optional async callable that persists one inbound
            bridge message and returns the stored internal message ID.
            save_error_response_fn: Optional callable used to persist fallback
                assistant error responses when orchestration fails.
            reload_llm_fn: Optional async callable that reloads the active
                LLM runtime from the latest saved configuration.
            list_run_events_fn: Optional async callable that returns runtime
                journal events for reconnect recovery.
            mcp_memory_remember_fn: Optional async callable that persists one MCP
                external-agent memory (store-only) and returns its ids.
            mcp_ask_fn: Optional async callable that resolves an agent session,
                runs the LLM agentic loop, and returns the answer.
            memory_invalidate_fn: Optional async callable that invalidates one
                graph assertion on user request (Socket-A write).
            tool_catalog_fn: Callable returning the finalized catalog projection.
            scheduled_tool_authorization_fn: Callable validating scheduler tools.
        """
        self._orchestrator_fn = orchestrator_fn
        self._confirm_fn = confirm_fn
        self._bridge_inbound_fn = bridge_inbound_fn
        self._save_error_response_fn = save_error_response_fn
        self._reload_llm_fn = reload_llm_fn
        self._list_run_events_fn = list_run_events_fn
        self._mcp_memory_remember_fn = mcp_memory_remember_fn
        self._mcp_ask_fn = mcp_ask_fn
        self._memory_invalidate_fn = memory_invalidate_fn
        self._tool_catalog_fn = tool_catalog_fn
        self._scheduled_tool_authorization_fn = scheduled_tool_authorization_fn
        self._active_task: asyncio.Task | None = None
        self._active_request_id = ""
        self._active_run_id = ""
        self._active_session_id = ""
        self._stopping_task: asyncio.Task | None = None

        self._server = IPCSocketServer(
            socket_path=socket_path,
            secret=secret,
            handler=self._handle_message,
            type_map=FRONTEND_TYPE_MAP,
        )

    async def start(self) -> None:
        """Start the IPC server."""
        await self._server.start()

    async def stop(self) -> None:
        """Stop the IPC server and cancel active tasks."""
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
        await self._server.stop()

    async def push_event(self, event: StreamEvent) -> None:
        """Push a streaming event to the connected FastAPI process.

        Args:
            event: StreamEvent to send.
        """
        if self._server.has_client:
            await self._server.push(event)

    async def _handle_message(
        self,
        type_name: str,
        message: BaseModel,
    ) -> BaseModel | None:
        """Handle incoming IPC message from FastAPI.

        Args:
            type_name: Message type name.
            message: Deserialized message.

        Returns:
            Response message or None.
        """
        handler = self._message_handlers().get(type_name)
        if handler is None:
            return None
        return await self._call_message_handler(handler, message)

    @staticmethod
    async def _call_message_handler(
        handler: Callable[[BaseModel], BaseModel | None],
        message: BaseModel,
    ) -> BaseModel | None:
        """Call one message handler and await its result when needed."""
        result = handler(message)
        if inspect.isawaitable(result):
            return await result
        return result

    def _message_handlers(
        self,
    ) -> dict[str, Callable[[BaseModel], BaseModel | None]]:
        """Return the per-type handler map for incoming IPC messages."""
        return {
            "ChatRequest": self._handle_chat,
            "ListAgentRunEventsRequest": self._handle_list_run_events,
            "ReloadLLMRequest": self._handle_reload_llm,
            "StopRequest": self._handle_stop,
            "ConfirmRequest": self._handle_confirm,
            "BridgeInboundSyncRequest": self._handle_bridge_inbound,
            "McpMemoryRememberRequest": self._handle_mcp_memory_remember,
            "McpAskRequest": self._handle_mcp_ask,
            "MemoryInvalidateRequest": self._handle_memory_invalidate,
            "ToolCatalogRequest": self._handle_tool_catalog,
            "ScheduledToolAuthorizationRequest": (
                self._handle_scheduled_tool_authorization
            ),
        }

    async def _handle_scheduled_tool_authorization(
        self,
        request: ScheduledToolAuthorizationRequest,
    ) -> ScheduledToolAuthorizationResponse:
        """Validate scheduler tools against the finalized registry snapshot."""
        try:
            if self._scheduled_tool_authorization_fn is None:
                raise RuntimeError("Scheduled tool authorization is unavailable")
            authorizations = self._scheduled_tool_authorization_fn(request.tool_names)
            if inspect.isawaitable(authorizations):
                authorizations = await authorizations
        except (RuntimeError, ValueError) as exc:
            return ScheduledToolAuthorizationResponse(
                request_id=request.request_id,
                error=str(exc),
            )
        return ScheduledToolAuthorizationResponse(
            request_id=request.request_id,
            authorizations=authorizations,
        )

    async def _handle_tool_catalog(
        self,
        request: ToolCatalogRequest,
    ) -> ToolCatalogResponse:
        """Return the catalog from the finalized runtime registry."""
        tools = [] if self._tool_catalog_fn is None else self._tool_catalog_fn()
        if inspect.isawaitable(tools):
            tools = await tools
        return ToolCatalogResponse(request_id=request.request_id, tools=tools)

    async def _handle_memory_invalidate(
        self,
        request: MemoryInvalidateRequest,
    ) -> MemoryInvalidateResponse:
        """Invalidate one graph assertion on user request.

        Memory writes are reserved for the orchestrator (Socket A); the
        web-facing FastAPI process only forwards the request here.
        """
        if self._memory_invalidate_fn is None:
            return MemoryInvalidateResponse(
                request_id=request.request_id, invalidated=False
            )
        return await self._memory_invalidate_fn(request)

    async def _handle_mcp_memory_remember(
        self,
        request: McpMemoryRememberRequest,
    ) -> McpMemoryRememberResponse:
        """Persist one MCP external-agent memory in the orchestrator process.

        Memory-content writes are reserved for the orchestrator (Socket A);
        the web-facing FastAPI process never writes memory directly.
        """
        if self._mcp_memory_remember_fn is None:
            return McpMemoryRememberResponse(
                request_id=request.request_id, stored=False
            )
        return await self._mcp_memory_remember_fn(request)

    async def _handle_mcp_ask(
        self,
        request: McpAskRequest,
    ) -> McpAskResponse:
        """Handle an MCP ask request — run LLM over the agent's session.

        Resolves the agent session, builds a ChatRequest, runs the
        normal agentic loop, and returns the answer. Never calls
        provider.send_message (MCP is pull-only).
        """
        if self._mcp_ask_fn is None:
            return McpAskResponse(
                request_id=request.request_id,
                error="ask handler unavailable",
                error_code="ask_handler_unavailable",
            )
        return await self._mcp_ask_fn(request)

    @staticmethod
    def _stop_response(
        request_id: str,
        *,
        stopped: bool,
        run_id: str = "",
    ) -> StopResponse:
        """Build one stop response."""
        return StopResponse(
            request_id=request_id,
            stopped=stopped,
            run_id=run_id,
        )

    @staticmethod
    def _bridge_sync_response(
        request_id: str,
        *,
        conversation_id: str,
        message_id: str,
        synced: bool,
        reason: str,
        assistant_content: str = "",
        assistant_message_id: str = "",
    ) -> BridgeInboundSyncResponse:
        """Build one inbound bridge sync response."""
        return BridgeInboundSyncResponse(
            request_id=request_id,
            conversation_id=conversation_id,
            message_id=message_id,
            synced=synced,
            reason=reason,
            assistant_content=assistant_content,
            assistant_message_id=assistant_message_id,
        )

    async def _call_confirm_fn(
        self,
        request: ConfirmRequest,
    ) -> ConfirmResponse:
        """Call the configured confirmation handler."""
        if self._confirm_fn is None:
            return ConfirmResponse(
                request_id=request.request_id,
                action_id=request.action_id,
                accepted=False,
            )
        return await self._confirm_fn(request)

    async def _call_bridge_inbound_fn(
        self,
        request: BridgeInboundSyncRequest,
    ) -> object:
        """Call the configured inbound bridge sync handler."""
        if self._bridge_inbound_fn is None:
            raise ChatBridgeSyncSkippedError("bridge_unavailable")
        return await self._bridge_inbound_fn(request)

    async def _call_reload_llm_fn(
        self,
        request: ReloadLLMRequest,
    ) -> ReloadLLMResponse:
        """Call the configured LLM reload handler."""
        if self._reload_llm_fn is None:
            return ReloadLLMResponse(
                request_id=request.request_id,
                success=False,
                error="reload handler unavailable",
            )
        response = await self._reload_llm_fn()
        return response.model_copy(update={"request_id": request.request_id})

    async def _handle_list_run_events(
        self,
        request: ListAgentRunEventsRequest,
    ) -> ListAgentRunEventsResponse:
        """Return runtime journal events for reconnect recovery."""
        if self._list_run_events_fn is None:
            return ListAgentRunEventsResponse(request_id=request.request_id)
        events = await self._list_run_events_fn(request)
        return ListAgentRunEventsResponse(
            request_id=request.request_id,
            events=events if isinstance(events, list) else [],
        )

    async def _handle_chat(self, request: ChatRequest) -> ChatResponse | None:
        """Process a chat request through the orchestrator.

        Args:
            request: Chat request from FastAPI.

        Returns:
            ``None`` after scheduling the chat task. The task pushes the
            ``ChatResponse`` over the same full-duplex IPC channel.
        """
        if self._active_task is not None and not self._active_task.done():
            return ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(
                    answer="Internal error: another chat request is already running"
                ),
            )
        self._active_task = asyncio.create_task(
            self._run_chat_task(request),
        )
        self._active_request_id = request.request_id
        self._active_run_id = request.run_id
        self._active_session_id = request.session_id
        self._active_task.add_done_callback(self._clear_finished_chat_task)
        return None

    async def _run_chat_task(self, request: ChatRequest) -> None:
        """Run one chat request and push its response when it finishes."""
        try:
            response = await self._orchestrator_fn(request)
        except Exception as error:
            logger.error("Orchestrator error: %s", error, exc_info=True)
            error_text = user_safe_error_text(error)
            await self._save_error_response(request, error_text)
            response = ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer=error_text),
            )
        await self._server.push(response)

    def _clear_finished_chat_task(self, task: asyncio.Task) -> None:
        """Forget the active chat task once it is no longer running."""
        if self._active_task is task:
            self._active_task = None
            self._active_request_id = ""
            self._active_run_id = ""
            self._active_session_id = ""
        if self._stopping_task is task:
            self._stopping_task = None

    async def _save_error_response(
        self,
        request: ChatRequest,
        error_text: str,
    ) -> None:
        """Persist an assistant error reply when normal orchestration fails."""
        if self._save_error_response_fn is None:
            return

        try:
            await self._persist_assistant_error_response(
                conversation_id=self._resolve_error_response_conversation_id(
                    request,
                ),
                error_text=error_text,
            )
        except Exception:
            logger.warning(
                "Failed to persist fallback assistant error response",
                exc_info=True,
            )

    @staticmethod
    def _resolve_error_response_conversation_id(
        request: ChatRequest,
    ) -> str:
        """Choose the conversation identifier used for fallback error persistence."""
        return request.conversation_id or request.session_id

    async def _persist_assistant_error_response(
        self,
        *,
        conversation_id: str,
        error_text: str,
    ) -> None:
        """Persist one fallback assistant error response through the save callback."""
        await await_maybe(
            self._save_error_response_fn,
            conversation_id,
            "assistant",
            error_text,
        )

    async def _handle_stop(self, request: StopRequest) -> StopResponse:
        """Cancel the active agentic loop.

        Args:
            request: Stop request from FastAPI.

        Returns:
            StopResponse confirming cancellation.
        """
        if (
            self._active_task is not None
            and not self._active_task.done()
            and request.target_request_id == self._active_request_id
            and request.run_id == self._active_run_id
            and request.session_id == self._active_session_id
        ):
            active_task = self._active_task
            if self._stopping_task is not active_task:
                self._stopping_task = active_task
                active_task.cancel()
            # The chat task may finish in the cancelled state when Stop
            # arrives before the turn row exists; the Stop reply must
            # still be delivered.
            with contextlib.suppress(asyncio.CancelledError):
                await active_task
            return self._stop_response(
                request.request_id,
                stopped=True,
                run_id=request.run_id,
            )
        return self._stop_response(
            request.request_id,
            stopped=False,
            run_id=request.run_id,
        )

    async def _handle_reload_llm(
        self,
        request: ReloadLLMRequest,
    ) -> ReloadLLMResponse:
        """Reload the active LLM runtime from the latest saved configuration."""
        try:
            return await self._call_reload_llm_fn(request)
        except Exception as error:
            logger.error("Reload LLM error: %s", error, exc_info=True)
            return ReloadLLMResponse(
                request_id=request.request_id,
                success=False,
                error=str(error),
            )

    async def _handle_confirm(
        self,
        request: ConfirmRequest,
    ) -> ConfirmResponse:
        """Process a trust confirmation decision.

        Args:
            request: Confirm request from FastAPI.

        Returns:
            ConfirmResponse with acceptance status.
        """
        try:
            return await self._call_confirm_fn(request)
        except Exception as error:
            logger.error("Confirm error: %s", error, exc_info=True)
            return ConfirmResponse(
                request_id=request.request_id,
                action_id=request.action_id,
                accepted=False,
            )

    async def _handle_bridge_inbound(
        self,
        request: BridgeInboundSyncRequest,
    ) -> BridgeInboundSyncResponse:
        """Persist one inbound bridge message through the write-capable path."""
        try:
            message_result = await self._call_bridge_inbound_fn(request)
        except ChatBridgeSyncSkippedError as error:
            return self._bridge_sync_response(
                request.request_id,
                conversation_id="",
                message_id="",
                synced=False,
                reason=error.reason,
            )
        except Exception as error:
            logger.error("Bridge inbound sync error: %s", error, exc_info=True)
            return self._bridge_sync_response(
                request.request_id,
                conversation_id="",
                message_id="",
                synced=False,
                reason="internal_error",
            )
        conversation_id = str(getattr(message_result, "conversation_id", ""))
        message_id = str(getattr(message_result, "message_id", ""))
        assistant_content = str(
            getattr(message_result, "assistant_content", ""),
        )
        assistant_message_id = str(
            getattr(message_result, "assistant_message_id", ""),
        )
        return self._bridge_sync_response(
            request.request_id,
            conversation_id=conversation_id,
            message_id=message_id,
            synced=True,
            reason="",
            assistant_content=assistant_content,
            assistant_message_id=assistant_message_id,
        )
