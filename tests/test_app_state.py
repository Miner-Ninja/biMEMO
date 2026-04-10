"""Tests for application state machine.

Covers state transitions, invalid transitions,
and state query properties.
"""

import pytest
from backend.app_state import (
    AppState,
    AppStateManager,
    InvalidTransitionError,
)


class TestAppState:
    """Tests for AppState enum."""

    def test_enum_values(self):
        """Verify enum string values."""
        assert AppState.SETUP == "setup"
        assert AppState.LOCKED == "locked"
        assert AppState.READY == "ready"

    def test_enum_members(self):
        """Verify all expected members exist."""
        assert len(AppState) == 3


class TestAppStateManager:
    """Tests for state transitions."""

    def test_initial_state_setup(self):
        """Verify initial state is stored."""
        mgr = AppStateManager(AppState.SETUP)
        assert mgr.state == AppState.SETUP

    def test_initial_state_locked(self):
        """Verify locked initial state."""
        mgr = AppStateManager(AppState.LOCKED)
        assert mgr.state == AppState.LOCKED

    def test_setup_to_locked(self):
        """Verify SETUP → LOCKED transition."""
        mgr = AppStateManager(AppState.SETUP)
        mgr.to_locked()
        assert mgr.state == AppState.LOCKED

    def test_locked_to_ready(self):
        """Verify LOCKED → READY transition."""
        mgr = AppStateManager(AppState.LOCKED)
        mgr.to_ready()
        assert mgr.state == AppState.READY

    def test_setup_to_ready_invalid(self):
        """Verify SETUP → READY is invalid."""
        mgr = AppStateManager(AppState.SETUP)
        with pytest.raises(InvalidTransitionError):
            mgr.to_ready()

    def test_ready_to_locked_invalid(self):
        """Verify READY → LOCKED is invalid."""
        mgr = AppStateManager(AppState.READY)
        with pytest.raises(InvalidTransitionError):
            mgr.to_locked()

    def test_ready_to_ready_invalid(self):
        """Verify READY → READY is invalid."""
        mgr = AppStateManager(AppState.READY)
        with pytest.raises(InvalidTransitionError):
            mgr.to_ready()

    def test_locked_to_locked_invalid(self):
        """Verify LOCKED → LOCKED is invalid."""
        mgr = AppStateManager(AppState.LOCKED)
        with pytest.raises(InvalidTransitionError):
            mgr.to_locked()

    def test_is_setup(self):
        """Verify is_setup property."""
        mgr = AppStateManager(AppState.SETUP)
        assert mgr.is_setup
        assert not mgr.is_locked
        assert not mgr.is_ready

    def test_is_locked(self):
        """Verify is_locked property."""
        mgr = AppStateManager(AppState.LOCKED)
        assert not mgr.is_setup
        assert mgr.is_locked
        assert not mgr.is_ready

    def test_is_ready(self):
        """Verify is_ready property."""
        mgr = AppStateManager(AppState.READY)
        assert not mgr.is_setup
        assert not mgr.is_locked
        assert mgr.is_ready

    def test_full_lifecycle(self):
        """Verify full SETUP → LOCKED → READY lifecycle."""
        mgr = AppStateManager(AppState.SETUP)
        mgr.to_locked()
        mgr.to_ready()
        assert mgr.is_ready
