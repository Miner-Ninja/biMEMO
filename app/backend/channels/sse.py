"""SSE connection security for Web UI.

Enforces max connection lifetime, JWT re-validation,
and single connection per session. Integrates with
SessionManager for global session limit enforcement.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.channels.jwt_auth import (
        SessionManager,
    )


logger = logging.getLogger(__name__)

# SSE connection limits
SSE_MAX_LIFETIME = 300  # 5 minutes
SSE_MAX_CONNECTIONS = 3


@dataclass(frozen=True)
class SSEConnection:
    """Active SSE connection state.

    Attributes:
        session_jti: JWT token identifier.
        started_at: Connection start timestamp.
        token_exp: JWT expiration timestamp.
    """

    session_jti: str
    started_at: float
    token_exp: float


@dataclass
class SSEManager:
    """Manages active SSE connections.

    Enforces single connection per session and
    max lifetime limits.

    Attributes:
        max_lifetime: Maximum connection lifetime.
    """

    max_lifetime: int = SSE_MAX_LIFETIME
    _connections: dict[str, SSEConnection] = field(default_factory=dict)
    _session_managers: dict[str, SessionManager] = field(default_factory=dict)

    def connect(
        self,
        session_jti: str,
        token_exp: float,
        session_manager: SessionManager | None = None,
    ) -> SSEConnection | None:
        """Register a new SSE connection.

        Closes previous connection for the same session.
        Returns None if session or SSE limit exceeded.

        Args:
            session_jti: JWT token identifier.
            token_exp: JWT expiration timestamp.
            session_manager: Optional session manager
                for global session limit check.

        Returns:
            SSEConnection if accepted, None if rejected.
        """
        if session_manager is not None and not session_manager.register_session(
            session_jti, token_exp
        ):
            return None
        self._prune_expired()
        if session_jti in self._connections:
            self._remove_connection(session_jti)
        if len(self._connections) >= SSE_MAX_CONNECTIONS:
            if session_manager is not None:
                session_manager.unregister_session(session_jti)
            return None
        conn = SSEConnection(
            session_jti=session_jti,
            started_at=time.time(),
            token_exp=token_exp,
        )
        self._connections[session_jti] = conn
        if session_manager is not None:
            self._session_managers[session_jti] = session_manager
        return conn

    def _now(self) -> float:
        """Return the current wall-clock timestamp."""
        return time.time()

    def _is_expired(self, conn: SSEConnection, *, now: float) -> bool:
        """Return whether one SSE connection is expired."""
        return now - conn.started_at > self.max_lifetime or now > conn.token_exp

    def disconnect(self, session_jti: str) -> bool:
        """Remove an SSE connection.

        Args:
            session_jti: JWT token identifier.

        Returns:
            True if connection existed.
        """
        if session_jti in self._connections:
            self._remove_connection(session_jti)
            return True
        return False

    def is_valid(self, session_jti: str) -> bool:
        """Check if a connection is still valid.

        Validates lifetime and token expiration.

        Args:
            session_jti: JWT token identifier.

        Returns:
            True if connection is valid.
        """
        conn = self._connections.get(session_jti)
        if conn is None:
            return False
        now = self._now()
        if self._is_expired(conn, now=now):
            self._remove_connection(session_jti)
            return False
        return True

    @property
    def active_count(self) -> int:
        """Return number of active connections."""
        return len(self._connections)

    def _prune_expired(self) -> None:
        """Remove expired connections."""
        now = self._now()
        expired = [
            jti
            for jti, conn in self._connections.items()
            if self._is_expired(conn, now=now)
        ]
        for jti in expired:
            self._remove_connection(jti)

    def _remove_connection(self, session_jti: str) -> None:
        """Remove connection state and unregister the session if tracked."""
        self._connections.pop(session_jti, None)
        session_manager = self._session_managers.pop(session_jti, None)
        if session_manager is not None:
            session_manager.unregister_session(session_jti)
