"""Wiring tests for the graph-era memory maintenance cycle.

Behavioural pruning coverage lives in tests/test_graph_lifecycle.py;
this module asserts the cycle stays wired into the runtime.
"""

from backend.memory.runtime import scheduler
from backend.processes.db_proxy.registry import SOCKET_A_OPERATIONS


class TestPruneWiring:
    """The maintenance operations stay reachable from the hourly loop."""

    def test_prune_operations_on_socket_a(self):
        """Both prune operations are orchestrator-channel operations."""
        assert "prune_unlinked_records" in SOCKET_A_OPERATIONS
        assert "prune_graph" in SOCKET_A_OPERATIONS

    def test_pruning_loop_exists(self):
        """The hourly pruning loop is part of the scheduler runtime."""
        assert hasattr(scheduler, "run_pruning_loop")
        assert scheduler.PRUNING_INTERVAL_SECONDS == 3600.0
