"""Tests for the memory-event SSE route (memory-sse-stream, R1).

Drives the route's streaming generator directly: an event published to
the subscriber queue is delivered as a data frame, and the queue is
always unsubscribed when the generator finishes.
"""

import asyncio

import pytest
from backend.processes.fastapi_app.routes.admin_memory import stream_memory_events


class _FakeAuthState:
    def __init__(self, authed: bool = True) -> None:
        self._state = {"claims": {"sub": "admin"}} if authed else {}


class _FakeRequest:
    def __init__(self, authed: bool = True) -> None:
        self.state = _FakeAuthState(authed)
        self._disconnected = False

    async def is_disconnected(self) -> bool:
        return self._disconnected


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self.subscribed = 0
        self.unsubscribed = 0

    def subscribe_memory_events(self) -> asyncio.Queue:
        self.subscribed += 1
        return self.queue

    def unsubscribe_memory_events(self, queue: asyncio.Queue) -> None:
        assert queue is self.queue
        self.unsubscribed += 1


class TestMemorySseRoute:
    @pytest.mark.asyncio
    async def test_delivers_event_and_unsubscribes(self) -> None:
        """S1.1 + S1.2 — event arrives as a data frame; queue is freed."""
        orch = _FakeOrchestrator()
        request = _FakeRequest()
        await orch.queue.put({"type": "graph_extractor", "assertion_id": "a1"})

        response = await stream_memory_events(request, orch)
        assert response.media_type == "text/event-stream"
        assert orch.subscribed == 1

        frames: list[str] = []
        agen = response.body_iterator
        async for chunk in agen:
            text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
            frames.append(text)
            if text.startswith("data:"):
                # Got the event; simulate the client going away, then let
                # the generator observe it on the next loop and finish.
                request._disconnected = True
                await agen.aclose()
                break

        data_frames = [f for f in frames if f.startswith("data:")]
        assert len(data_frames) == 1
        assert '"assertion_id": "a1"' in data_frames[0]
        assert data_frames[0].endswith("\n\n")
        assert orch.unsubscribed == 1

    @pytest.mark.asyncio
    async def test_requires_auth(self) -> None:
        """S1.3 — an unauthenticated request is rejected before streaming."""
        from fastapi import HTTPException

        orch = _FakeOrchestrator()
        with pytest.raises(HTTPException) as exc:
            await stream_memory_events(_FakeRequest(authed=False), orch)
        assert exc.value.status_code == 401
        assert orch.subscribed == 0
