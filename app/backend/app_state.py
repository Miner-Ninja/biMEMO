"""Application state machine with three states.

Manages transitions between SETUP, LOCKED, and READY states.
Each state determines which endpoints are available. Transitions
are enforced: SETUP → LOCKED → READY. No reverse transitions.
"""

import logging
from enum import StrEnum


logger = logging.getLogger(__name__)


class AppState(StrEnum):
    """Application lifecycle states.

    SETUP: No key file exists. Only /health and /setup available.
    LOCKED: Key file exists but not yet unlocked. Only /health and /unlock available.
    READY: System unlocked and fully operational. All endpoints available.
    """

    SETUP = "setup"
    LOCKED = "locked"
    READY = "ready"


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class AppStateManager:
    """Manages application state transitions.

    Enforces valid transitions: SETUP → LOCKED → READY.
    No reverse transitions are allowed.

    Attributes:
        state: Current application state.
    """

    def __init__(self, initial: AppState) -> None:
        """Initialize with the given state.

        Args:
            initial: Starting application state.
        """
        self._state = initial

    @property
    def state(self) -> AppState:
        """Return the current application state.

        Returns:
            The current AppState value.
        """
        return self._state

    def _is_state(self, expected: AppState) -> bool:
        """Return whether the application is currently in one state."""
        return self._state == expected

    def _transition(
        self,
        *,
        expected: AppState,
        target: AppState,
    ) -> None:
        """Move to a new state after validating the current state."""
        if self._state != expected:
            raise InvalidTransitionError(
                f"Cannot transition to {target.name} from {self._state}"
            )
        self._state = target
        logger.info(
            "State transition: %s → %s",
            expected.name,
            target.name,
        )

    def to_locked(self) -> None:
        """Transition from SETUP to LOCKED.

        Raises:
            InvalidTransitionError: If current state is not SETUP.
        """
        self._transition(
            expected=AppState.SETUP,
            target=AppState.LOCKED,
        )

    def to_ready(self) -> None:
        """Transition from LOCKED to READY.

        Raises:
            InvalidTransitionError: If current state is not LOCKED.
        """
        self._transition(
            expected=AppState.LOCKED,
            target=AppState.READY,
        )

    @property
    def is_setup(self) -> bool:
        """Check if in SETUP state.

        Returns:
            True if state is SETUP.
        """
        return self._is_state(AppState.SETUP)

    @property
    def is_locked(self) -> bool:
        """Check if in LOCKED state.

        Returns:
            True if state is LOCKED.
        """
        return self._is_state(AppState.LOCKED)

    @property
    def is_ready(self) -> bool:
        """Check if in READY state.

        Returns:
            True if state is READY.
        """
        return self._is_state(AppState.READY)
