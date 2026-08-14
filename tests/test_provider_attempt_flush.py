"""Provider-attempt telemetry flush: authoritative vs outcome-preserving."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.agent.agent_loop import AgentOrchestrator


def _stub_with_tasks(tasks: list[asyncio.Task]) -> SimpleNamespace:
    """Build a minimal object carrying the pending telemetry task list."""
    return SimpleNamespace(_provider_attempt_tasks=tasks)


async def _failing_write() -> None:
    raise RuntimeError("db proxy unavailable")


async def _ok_write() -> None:
    return None


class TestAuthoritativeFlush:
    """The hard flush must surface storage failures before a FINAL commit."""

    @pytest.mark.asyncio
    async def test_flush_raises_on_write_failure(self):
        """A failed telemetry write propagates out of the hard flush."""
        stub = _stub_with_tasks([asyncio.create_task(_failing_write())])
        with pytest.raises(RuntimeError, match="db proxy unavailable"):
            await AgentOrchestrator._flush_provider_attempts(stub)
        assert stub._provider_attempt_tasks == []

    @pytest.mark.asyncio
    async def test_flush_passes_when_all_writes_land(self):
        """A clean queue flushes without raising."""
        stub = _stub_with_tasks([asyncio.create_task(_ok_write())])
        await AgentOrchestrator._flush_provider_attempts(stub)
        assert stub._provider_attempt_tasks == []


class TestOutcomePreservingFlush:
    """The logged flush must never replace an already-decided outcome."""

    @pytest.mark.asyncio
    async def test_write_failure_is_logged_not_raised(self, caplog):
        """A failed write becomes an ERROR log entry, not an exception."""
        stub = _stub_with_tasks(
            [
                asyncio.create_task(_failing_write()),
                asyncio.create_task(_ok_write()),
            ]
        )
        with caplog.at_level("ERROR"):
            await AgentOrchestrator._flush_provider_attempts_logged(stub)
        assert stub._provider_attempt_tasks == []
        assert any(
            "provider-attempt telemetry" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_all_tasks_awaited_despite_failure(self):
        """Sibling writes still complete when one write fails."""
        finished: list[str] = []

        async def slow_ok() -> None:
            await asyncio.sleep(0.01)
            finished.append("ok")

        stub = _stub_with_tasks(
            [
                asyncio.create_task(_failing_write()),
                asyncio.create_task(slow_ok()),
            ]
        )
        await AgentOrchestrator._flush_provider_attempts_logged(stub)
        assert finished == ["ok"]

    @pytest.mark.asyncio
    async def test_empty_queue_is_noop(self):
        """An empty queue flushes without side effects."""
        stub = _stub_with_tasks([])
        await AgentOrchestrator._flush_provider_attempts_logged(stub)
        assert stub._provider_attempt_tasks == []
