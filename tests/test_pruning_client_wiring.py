"""Connective tests: the pruning cycle against the real DBProxyClient class.

The stage-4 pruning loop crashed live because ``pruning.py`` called a
client method that was never written; unit tests missed it because they
used auto-attribute mocks. These tests run the cycle against the real
``DBProxyClient`` with only the transport (``send_operation``) stubbed,
so a missing client method fails loudly.
"""

from unittest.mock import AsyncMock

import pytest

from backend.memory.storage.pruning import (
    RECORD_PRUNE_AGE_DAYS,
    run_pruning_cycle,
)
from backend.processes.orchestrator.client import DBProxyClient


def _real_client_with_stubbed_transport() -> tuple[DBProxyClient, AsyncMock]:
    """Build a real DBProxyClient whose send_operation is recorded."""
    client = DBProxyClient(ipc_client=AsyncMock())
    transport = AsyncMock(
        side_effect=lambda operation, parameters=None, timeout=None: {
            "prune_unlinked_records": {"deleted": 2},
            "prune_graph": {"assertions_deleted": 1, "entities_deleted": 3},
        }[operation]
    )
    client.send_operation = transport  # type: ignore[method-assign]
    return client, transport


class TestPruningClientWiring:
    """run_pruning_cycle must work against the real client class."""

    @pytest.mark.asyncio
    async def test_cycle_runs_against_real_client(self) -> None:
        """Both prune operations reach the transport with real methods."""
        client, transport = _real_client_with_stubbed_transport()
        assertions, records, entities = await run_pruning_cycle(client)
        assert (assertions, records, entities) == (1, 2, 3)
        operations = [call.args[0] for call in transport.await_args_list]
        assert operations == ["prune_unlinked_records", "prune_graph"]

    @pytest.mark.asyncio
    async def test_record_prune_sends_age_parameter(self) -> None:
        """The record prune carries the retention age to the DB proxy."""
        client, transport = _real_client_with_stubbed_transport()
        await run_pruning_cycle(client)
        first_call = transport.await_args_list[0]
        assert first_call.args[1] == {"age_days": RECORD_PRUNE_AGE_DAYS}
