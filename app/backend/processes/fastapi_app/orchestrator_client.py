"""Multiplexing IPC client for the Orchestrator process.

Runs a background reader loop that dispatches incoming messages:
StreamEvents go to per-session queues for realtime forwarding, response
messages resolve pending futures for request handlers. Manages
session-to-conversation bindings and memory event fan-out.
"""

# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.ipc.ipc_channel import (
    IPCChannelError,
    IPCSocketClient,
)
from backend.ipc.models import (
    BridgeInboundSyncRequest as IPCBridgeInboundSyncRequest,
)
from backend.ipc.models import (
    BridgeInboundSyncResponse as IPCBridgeInboundSyncResponse,
)
from backend.ipc.models import (
    ChatRequest as IPCChatRequest,
)
from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    ConfirmRequest as IPCConfirmRequest,
)
from backend.ipc.models import (
    ConfirmResponse as IPCConfirmResponse,
)
from backend.ipc.models import (
    ListAgentRunEventsRequest as IPCListAgentRunEventsRequest,
)
from backend.ipc.models import (
    ListAgentRunEventsResponse as IPCListAgentRunEventsResponse,
)
from backend.ipc.models import (
    McpAskRequest as IPCMcpAskRequest,
)
from backend.ipc.models import (
    McpAskResponse as IPCMcpAskResponse,
)
from backend.ipc.models import (
    McpMemoryRememberRequest as IPCMcpMemoryRememberRequest,
)
from backend.ipc.models import (
    McpMemoryRememberResponse as IPCMcpMemoryRememberResponse,
)
from backend.ipc.models import (
    MemoryInvalidateRequest as IPCMemoryInvalidateRequest,
)
from backend.ipc.models import (
    MemoryInvalidateResponse as IPCMemoryInvalidateResponse,
)
from backend.ipc.models import (
    ReloadLLMRequest as IPCReloadLLMRequest,
)
from backend.ipc.models import (
    ReloadLLMResponse as IPCReloadLLMResponse,
)
from backend.ipc.models import (
    StopRequest as IPCStopRequest,
)
from backend.ipc.models import (
    StopResponse as IPCStopResponse,
)
from backend.ipc.models import (
    StreamEvent,
    StreamEventType,
)
from backend.ipc.models import (
    StructuredAssistantContent as IPCStructuredAssistantContent,
)


if TYPE_CHECKING:
    from pydantic import BaseModel


logger = logging.getLogger(__name__)

# IPC type map for incoming orchestrator messages
ORCHESTRATOR_RESPONSE_TYPES: dict[str, type[BaseModel]] = {
    "BridgeInboundSyncResponse": IPCBridgeInboundSyncResponse,
    "ChatResponse": IPCChatResponse,
    "ListAgentRunEventsResponse": IPCListAgentRunEventsResponse,
    "McpAskResponse": IPCMcpAskResponse,
    "McpMemoryRememberResponse": IPCMcpMemoryRememberResponse,
    "MemoryInvalidateResponse": IPCMemoryInvalidateResponse,
    "ReloadLLMResponse": IPCReloadLLMResponse,
    "StopResponse": IPCStopResponse,
    "ConfirmResponse": IPCConfirmResponse,
    "StreamEvent": StreamEvent,
}


@dataclass(frozen=True)
class RealtimeSubscriber:
    """One browser realtime subscriber bound to an optional conversation."""

    queue: asyncio.Queue
    conversation_id: str = ""


class OrchestratorClient:
    """Multiplexing IPC client for the Orchestrator process.

    Runs a background reader loop that dispatches incoming messages:
    StreamEvents go to per-session queues for realtime forwarding,
    response messages resolve pending futures for request handlers.

    Attributes:
        _ipc: Underlying IPC socket client.
        _connected: Whether the reader is active.
    """

    def __init__(self, ipc_client: IPCSocketClient) -> None:
        """Initialize the multiplexing client.

        Args:
            ipc_client: Connected IPCSocketClient to Orchestrator.
        """
        self._ipc = ipc_client
        self._response_futures: dict[str, asyncio.Future] = {}
        self._session_queues: dict[str, list[RealtimeSubscriber]] = {}
        self._request_to_session: dict[str, str] = {}
        self._request_to_conversation: dict[str, str] = {}
        self._active_chat_request_id = ""
        self._stopped_chat_request_ids: set[str] = set()
        self._memory_event_queues: list[asyncio.Queue] = []
        self._reader_task: asyncio.Task | None = None
        self._connected = False
        self._is_processing = False

    async def start(self) -> None:
        """Start the background message reader."""
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """Stop the reader and close the connection."""
        self._connected = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        await self._ipc.close()

    @property
    def is_connected(self) -> bool:
        """Check if the client is actively connected."""
        return self._connected

    @property
    def is_processing(self) -> bool:
        """Check if a chat request is in progress."""
        return self._is_processing

    def register_chat_request(
        self,
        request_id: str,
        session_id: str,
        conversation_id: str = "",
    ) -> None:
        """Mark a chat request active before its background task starts."""
        self._request_to_session[request_id] = session_id
        if conversation_id:
            self._request_to_conversation[request_id] = conversation_id
        self._active_chat_request_id = request_id
        self._is_processing = True

    def register_session_queue(
        self,
        session_id: str,
        queue: asyncio.Queue,
        conversation_id: str = "",
    ) -> None:
        """Register a queue for receiving stream events for a session.

        Args:
            session_id: Session identifier.
            queue: Queue to receive StreamEvent messages.
            conversation_id: Currently open chat id for direct chat events.
        """
        subscriber = RealtimeSubscriber(
            queue=queue,
            conversation_id=conversation_id.strip(),
        )
        self._session_queues.setdefault(session_id, []).append(subscriber)

    def unregister_session_queue(
        self,
        session_id: str,
        queue: asyncio.Queue | None = None,
    ) -> None:
        """Unregister a session's stream event queue.

        Args:
            session_id: Session identifier.
            queue: Optional queue that must still be current.
        """
        subscribers = self._session_queues.get(session_id)
        if not subscribers:
            return
        if queue is None:
            self._session_queues.pop(session_id, None)
            return
        remaining = [
            subscriber for subscriber in subscribers if subscriber.queue is not queue
        ]
        if remaining:
            self._session_queues[session_id] = remaining
            return
        self._session_queues.pop(session_id, None)

    async def publish_conversation_event(
        self,
        *,
        conversation_id: str,
        event_type: StreamEventType,
        data: dict[str, Any],
    ) -> None:
        """Push one realtime event into the browser that currently shows this chat."""
        subscribers = self._subscribers_for_conversation(conversation_id)
        event = StreamEvent(
            request_id="",
            event_type=event_type,
            data=data,
        )
        for subscriber in subscribers:
            await subscriber.queue.put(event)

    def subscribe_memory_events(self) -> asyncio.Queue:
        """Subscribe to memory pipeline events.

        Returns:
            Queue that receives memory event dicts.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._memory_event_queues.append(queue)
        return queue

    def unsubscribe_memory_events(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from memory pipeline events.

        Args:
            queue: Queue previously returned by subscribe_memory_events.
        """
        with suppress(ValueError):
            self._memory_event_queues.remove(queue)

    async def _read_loop(self) -> None:
        """Continuously read messages and dispatch to waiters."""
        try:
            while self._connected:
                message = await self._ipc.receive()
                if isinstance(message, StreamEvent):
                    await self._handle_stream_event(message)
                elif isinstance(
                    message,
                    (
                        IPCBridgeInboundSyncResponse,
                        IPCChatResponse,
                        IPCListAgentRunEventsResponse,
                        IPCMcpAskResponse,
                        IPCMcpMemoryRememberResponse,
                        IPCMemoryInvalidateResponse,
                        IPCReloadLLMResponse,
                        IPCStopResponse,
                        IPCConfirmResponse,
                    ),
                ):
                    self._resolve_response_future(message)
        except (
            IPCChannelError,
            ConnectionResetError,
            asyncio.IncompleteReadError,
        ):
            self._handle_disconnect()
        except asyncio.CancelledError:
            pass

    async def send_chat(
        self,
        request: IPCChatRequest,
        session_id: str,
        timeout: float = 300.0,
    ) -> IPCChatResponse:
        """Send a ChatRequest and wait for the ChatResponse.

        Args:
            request: Chat request to send.
            session_id: Session ID for stream routing.
            timeout: Maximum wait time in seconds.

        Returns:
            ChatResponse from the Orchestrator.

        Raises:
            IPCChannelError: If not connected.
            asyncio.TimeoutError: If response not received in time.
        """
        self._require_connected()

        if request.request_id in self._stopped_chat_request_ids:
            self._stopped_chat_request_ids.discard(request.request_id)
            self._request_to_session.pop(request.request_id, None)
            self._request_to_conversation.pop(request.request_id, None)
            if self._active_chat_request_id == request.request_id:
                self._active_chat_request_id = ""
            self._is_processing = False
            return IPCChatResponse(
                request_id=request.request_id,
                structured_content=IPCStructuredAssistantContent(answer="Stopped."),
            )

        self._request_to_session[request.request_id] = session_id
        if request.conversation_id:
            self._request_to_conversation[request.request_id] = request.conversation_id
        self._active_chat_request_id = request.request_id
        self._is_processing = True
        try:
            return await self._send_request_and_wait(
                request=request,
                timeout=timeout,
            )
        except TimeoutError:
            raise
        finally:
            self._request_to_session.pop(request.request_id, None)
            self._request_to_conversation.pop(request.request_id, None)
            if self._active_chat_request_id == request.request_id:
                self._active_chat_request_id = ""
            self._is_processing = False

    async def send_bridge_inbound_sync(
        self,
        request: IPCBridgeInboundSyncRequest,
        timeout: float = 30.0,
    ) -> IPCBridgeInboundSyncResponse:
        """Send one inbound bridge message for persistence via Orchestrator."""
        self._require_connected()

        try:
            return await self._send_request_and_wait(
                request=request,
                timeout=timeout,
            )
        except TimeoutError:
            raise

    async def mcp_memory_remember(
        self,
        *,
        content: str,
        channel: str,
        agent_id: str = "",
        timeout: float = 10.0,
    ) -> IPCMcpMemoryRememberResponse:
        """Store one MCP external-agent memory via the Orchestrator.

        The write executes in the orchestrator process (Socket A); the
        web-facing FastAPI process never writes memory content directly.

        Args:
            content: Plain text content to store.
            channel: Provenance channel identifying the external agent.
            agent_id: Stable identifier for the requesting agent.
            timeout: Maximum wait time in seconds.

        Returns:
            McpMemoryRememberResponse with the stored message and conversation ids.
        """
        self._require_connected()
        request = IPCMcpMemoryRememberRequest(
            request_id=str(uuid.uuid4()),
            content=content,
            channel=channel,
            agent_id=agent_id,
        )
        return await self._send_request_and_wait(
            request=request,
            timeout=timeout,
        )

    async def memory_invalidate(
        self,
        *,
        assertion_id: str,
        reason: str,
        timeout: float = 10.0,
    ) -> IPCMemoryInvalidateResponse:
        """Invalidate one graph assertion via the Orchestrator.

        The write executes in the orchestrator process (Socket A); the
        web-facing FastAPI process never writes memory content directly.

        Args:
            assertion_id: Assertion to invalidate.
            reason: User-stated reason ("forget" or "incorrect"), logged
                for diagnostics.
            timeout: Maximum wait time in seconds.

        Returns:
            MemoryInvalidateResponse with the invalidation result.
        """
        self._require_connected()
        request = IPCMemoryInvalidateRequest(
            request_id=str(uuid.uuid4()),
            assertion_id=assertion_id,
            reason=reason,
        )
        return await self._send_request_and_wait(
            request=request,
            timeout=timeout,
        )

    async def mcp_ask(
        self,
        *,
        question: str,
        agent_id: str = "",
        timeout: float = 300.0,
    ) -> IPCMcpAskResponse:
        """Ask biMEMO a question via an MCP external agent.

        Resolves the agent session in the orchestrator and returns
        the LLM answer. Never uses outbound send (MCP is pull-only).

        Args:
            question: The question text to ask.
            agent_id: Stable identifier for the requesting agent.
            timeout: Maximum wait time in seconds.

        Returns:
            McpAskResponse with the answer text.
        """
        self._require_connected()
        request = IPCMcpAskRequest(
            request_id=str(uuid.uuid4()),
            question=question,
            agent_id=agent_id,
        )
        return await self._send_request_and_wait(
            request=request,
            timeout=timeout,
        )


    async def list_agent_run_events(
        self,
        *,
        conversation_id: str = "",
        run_id: str = "",
        after_sequence: int = 0,
        limit: int = 200,
        timeout: float = 10.0,
    ) -> IPCListAgentRunEventsResponse:
        """Request persisted runtime events from the orchestrator process."""
        self._require_connected()
        request = IPCListAgentRunEventsRequest(
            request_id=str(uuid.uuid4()),
            run_id=run_id,
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return await self._send_request_and_wait(
            request=request,
            timeout=timeout,
        )

    async def send_stop(
        self,
        request: IPCStopRequest,
        timeout: float = 10.0,
    ) -> IPCStopResponse:
        """Send a StopRequest and wait for the StopResponse.

        Args:
            request: Stop request to send.
            timeout: Maximum wait time in seconds.

        Returns:
            StopResponse from the Orchestrator.

        Raises:
            IPCChannelError: If not connected.
        """
        self._require_connected()

        try:
            response = await self._send_request_and_wait(
                request=request,
                timeout=timeout,
            )
            if response.stopped:
                self._release_stopped_chat_request(request.request_id)
            return response
        except TimeoutError:
            raise

    async def send_reload_llm(
        self,
        request_id: str,
        timeout: float = 30.0,
    ) -> IPCReloadLLMResponse:
        """Send one runtime LLM reload request and wait for the response."""
        self._require_connected()

        request = IPCReloadLLMRequest(request_id=request_id)
        try:
            return await self._send_request_and_wait(
                request=request,
                timeout=timeout,
            )
        except TimeoutError:
            raise

    async def send_confirm(
        self,
        request: IPCConfirmRequest,
        timeout: float = 130.0,
    ) -> IPCConfirmResponse:
        """Send a ConfirmRequest and wait for the ConfirmResponse.

        Args:
            request: Confirm request to send.
            timeout: Maximum wait time in seconds.

        Returns:
            ConfirmResponse from the Orchestrator.

        Raises:
            IPCChannelError: If not connected.
        """
        self._require_connected()

        try:
            return await self._send_request_and_wait(
                request=request,
                timeout=timeout,
            )
        except TimeoutError:
            raise

    async def _send_request_and_wait(
        self,
        *,
        request: BaseModel,
        timeout: float,
    ) -> BaseModel:
        """Send one IPC request and wait for its matching response."""
        response_future = self._register_response_future(request.request_id)
        try:
            await self._ipc.send(request)
            return await asyncio.wait_for(response_future, timeout=timeout)
        except TimeoutError:
            self._response_futures.pop(request.request_id, None)
            raise

    def _require_connected(self) -> None:
        """Raise when the orchestrator IPC reader is not connected."""
        if not self._connected:
            raise IPCChannelError("Not connected to Orchestrator")

    def _register_response_future(self, request_id: str) -> asyncio.Future:
        """Create and store one pending response future."""
        event_loop = asyncio.get_running_loop()
        response_future = event_loop.create_future()
        self._response_futures[request_id] = response_future
        return response_future

    def _release_stopped_chat_request(self, request_id: str) -> None:
        """Resolve the pending chat future after a successful stop command."""
        chat_request_id = self._resolve_stopped_chat_request_id(request_id)
        if not chat_request_id:
            return
        response_future = self._response_futures.pop(chat_request_id, None)
        if response_future is None:
            self._stopped_chat_request_ids.add(chat_request_id)
            self._request_to_session.pop(chat_request_id, None)
            self._request_to_conversation.pop(chat_request_id, None)
            if self._active_chat_request_id == chat_request_id:
                self._active_chat_request_id = ""
            self._is_processing = False
            return
        if response_future.done():
            return
        self._request_to_session.pop(chat_request_id, None)
        self._request_to_conversation.pop(chat_request_id, None)
        if self._active_chat_request_id == chat_request_id:
            self._active_chat_request_id = ""
        self._is_processing = False
        response_future.set_result(
            IPCChatResponse(
                request_id=chat_request_id,
                structured_content=IPCStructuredAssistantContent(answer="Stopped."),
            )
        )

    def _resolve_stopped_chat_request_id(self, request_id: str) -> str:
        """Find the pending chat request associated with one stop command."""
        if request_id in self._request_to_session:
            return request_id
        if self._active_chat_request_id in self._request_to_session:
            return self._active_chat_request_id
        return ""

    async def _handle_stream_event(self, message: StreamEvent) -> None:
        """Dispatch one incoming stream event to its target queue."""
        if message.event_type == "memory_event":
            self._publish_memory_event(message.data)
            return
        if message.event_type == "scheduled_task_result":
            await self._broadcast_to_all_sessions(message)
            return
        subscribers = self._resolve_session_subscribers(message.request_id)
        logger.debug(
            "Routing realtime event type=%s request_id=%s subscribers=%s",
            message.event_type,
            message.request_id,
            len(subscribers),
        )
        for subscriber in subscribers:
            await subscriber.queue.put(message)

    async def _broadcast_to_all_sessions(self, message: StreamEvent) -> None:
        """Fan out one stream event to every active realtime session queue.

        Used for events that aren't tied to a specific request_id, such
        as scheduled_task_result — they need to reach any browser tab
        that is currently connected, regardless of which conversation
        is open.
        """
        for subscribers in self._session_queues.values():
            for subscriber in subscribers:
                with suppress(asyncio.QueueFull):
                    subscriber.queue.put_nowait(message)

    def _publish_memory_event(self, data: dict[str, Any]) -> None:
        """Fan out one memory event to admin subscribers."""
        for memory_queue in self._memory_event_queues:
            with suppress(asyncio.QueueFull):
                memory_queue.put_nowait(data)

    def _resolve_session_subscribers(
        self,
        request_id: str,
    ) -> list[RealtimeSubscriber]:
        """Return realtime subscribers bound to one request id."""
        session_id = self._request_to_session.get(request_id)
        conversation_id = self._request_to_conversation.get(request_id, "")
        if session_id is not None:
            subscribers = self._subscribers_for_session(
                session_id,
                conversation_id=conversation_id,
            )
            if subscribers:
                return subscribers
        if not conversation_id:
            return []
        return self._subscribers_for_conversation(conversation_id)

    def _subscribers_for_session(
        self,
        session_id: str,
        *,
        conversation_id: str = "",
    ) -> list[RealtimeSubscriber]:
        """Return subscribers for one session, optionally scoped to a conversation."""
        subscribers = self._session_queues.get(session_id, [])
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id:
            return list(subscribers)
        return [
            subscriber
            for subscriber in subscribers
            if subscriber.conversation_id in ("", normalized_conversation_id)
        ]

    def _subscribers_for_conversation(
        self,
        conversation_id: str,
    ) -> list[RealtimeSubscriber]:
        """Return every active subscriber currently viewing one conversation."""
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id:
            return []
        return [
            subscriber
            for subscribers in self._session_queues.values()
            for subscriber in subscribers
            if subscriber.conversation_id == normalized_conversation_id
        ]

    def _resolve_response_future(self, message: BaseModel) -> None:
        """Resolve one pending response future from an incoming message."""
        response_future = self._response_futures.pop(message.request_id, None)
        if response_future is not None and not response_future.done():
            response_future.set_result(message)

    def _handle_disconnect(self) -> None:
        """Mark the client disconnected and fail all pending requests."""
        self._connected = False
        self._fail_pending_responses("Orchestrator disconnected")

    def _fail_pending_responses(self, error_message: str) -> None:
        """Fail all response futures with one shared IPC error."""
        for response_future in self._response_futures.values():
            if not response_future.done():
                response_future.set_exception(IPCChannelError(error_message))
        self._response_futures.clear()
