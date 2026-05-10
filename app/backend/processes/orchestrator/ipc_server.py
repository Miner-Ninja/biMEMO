"""IPC server for the Orchestrator ↔ FastAPI channel."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING

from backend.chat_bridge.service_models import ChatBridgeSyncSkippedError
from backend.ipc.ipc_channel import IPCSocketServer
from backend.ipc.models import (
    BridgeInboundSyncRequest,
    BridgeInboundSyncResponse,
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    ConfirmResponse,
    ReloadLLMRequest,
    ReloadLLMResponse,
    StopRequest,
    StopResponse,
    StreamEvent,
    StreamEventType,
    StructuredAssistantContent,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

logger = logging.getLogger(__name__)

FRONTEND_TYPE_MAP = {
    "BridgeInboundSyncRequest": BridgeInboundSyncRequest,
    "ChatRequest": ChatRequest,
    "ReloadLLMRequest": ReloadLLMRequest,
    "StopRequest": StopRequest,
    "ConfirmRequest": ConfirmRequest,
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
        """
        self._orchestrator_fn = orchestrator_fn
        self._confirm_fn = confirm_fn
        self._bridge_inbound_fn = bridge_inbound_fn
        self._save_error_response_fn = save_error_response_fn
        self._reload_llm_fn = reload_llm_fn
        self._active_task: asyncio.Task | None = None

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
            "ReloadLLMRequest": self._handle_reload_llm,
            "StopRequest": self._handle_stop,
            "ConfirmRequest": self._handle_confirm,
            "BridgeInboundSyncRequest": self._handle_bridge_inbound,
        }

    @staticmethod
    def _build_internal_error_text(error: Exception) -> str:
        """Build the short user-facing text for an internal failure."""
        return f"Internal error: {str(error)[:200]}"

    @staticmethod
    def _stop_response(request_id: str, *, stopped: bool) -> StopResponse:
        """Build one stop response."""
        return StopResponse(
            request_id=request_id,
            stopped=stopped,
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
        self._active_task.add_done_callback(self._clear_finished_chat_task)
        return None

    async def _run_chat_task(self, request: ChatRequest) -> None:
        """Run one chat request and push its response when it finishes."""
        try:
            response = await self._orchestrator_fn(request)
        except asyncio.CancelledError:
            response = ChatResponse(
                request_id=request.request_id,
                structured_content=StructuredAssistantContent(answer="Stopped."),
            )
        except Exception as error:
            logger.error("Orchestrator error: %s", error, exc_info=True)
            error_text = self._build_internal_error_text(error)
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
        if inspect.iscoroutinefunction(self._save_error_response_fn):
            await self._save_error_response_fn(
                conversation_id,
                "assistant",
                error_text,
            )
            return
        result = self._save_error_response_fn(
            conversation_id,
            "assistant",
            error_text,
        )
        if inspect.isawaitable(result):
            await result

    async def _handle_stop(self, request: StopRequest) -> StopResponse:
        """Cancel the active agentic loop.

        Args:
            request: Stop request from FastAPI.

        Returns:
            StopResponse confirming cancellation.
        """
        if self._active_task is not None and not self._active_task.done():
            active_task = self._active_task
            self._active_task = None
            active_task.cancel()
            await self.push_event(
                StreamEvent(
                    request_id=request.request_id,
                    event_type=StreamEventType.CONFIRMATION,
                    data={"status": "stopped"},
                )
            )
            return self._stop_response(request.request_id, stopped=True)
        return self._stop_response(request.request_id, stopped=False)

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
