"""Tests for SSE connection security.

Covers connection lifecycle, lifetime limits,
and single-connection-per-session enforcement.
"""

import time
from unittest.mock import patch

from backend.channels.jwt_auth import (
    MAX_SESSIONS,
    SessionManager,
)
from backend.channels.sse import (
    SSE_MAX_CONNECTIONS,
    SSE_MAX_LIFETIME,
    SSEConnection,
    SSEManager,
)


class TestSSEConnect:
    """Tests for SSE connection registration."""

    def test_connect_returns_connection(self):
        """Verify connect creates connection."""
        mgr = SSEManager()
        conn = mgr.connect("jti-1", time.time() + 900)
        assert isinstance(conn, SSEConnection)
        assert conn.session_jti == "jti-1"

    def test_replaces_same_session(self):
        """Verify new connection replaces old."""
        mgr = SSEManager()
        mgr.connect("jti-1", time.time() + 900)
        mgr.connect("jti-1", time.time() + 900)
        assert mgr.active_count == 1

    def test_rejects_over_max(self):
        """Verify rejects when max connections reached."""
        mgr = SSEManager()
        exp = time.time() + 900
        for i in range(SSE_MAX_CONNECTIONS):
            assert mgr.connect(f"jti-{i}", exp) is not None
        assert mgr.connect("extra", exp) is None

    def test_accepts_after_disconnect(self):
        """Verify accepts after disconnect frees slot."""
        mgr = SSEManager()
        exp = time.time() + 900
        for i in range(SSE_MAX_CONNECTIONS):
            mgr.connect(f"jti-{i}", exp)
        mgr.disconnect("jti-0")
        assert mgr.connect("new", exp) is not None


class TestSSEDisconnect:
    """Tests for SSE disconnection."""

    def test_disconnect_existing(self):
        """Verify disconnect removes connection."""
        mgr = SSEManager()
        mgr.connect("jti-1", time.time() + 900)
        assert mgr.disconnect("jti-1")
        assert mgr.active_count == 0

    def test_disconnect_nonexistent(self):
        """Verify disconnect on missing returns False."""
        mgr = SSEManager()
        assert not mgr.disconnect("none")


class TestSSEValidity:
    """Tests for SSE connection validity checks."""

    def test_valid_connection(self):
        """Verify fresh connection is valid."""
        mgr = SSEManager()
        mgr.connect("jti-1", time.time() + 900)
        assert mgr.is_valid("jti-1")

    def test_expired_lifetime(self):
        """Verify connection expired by lifetime."""
        mgr = SSEManager()
        mgr.connect("jti-1", time.time() + 900)
        with patch(
            "backend.channels.sse.time.time",
            return_value=time.time() + SSE_MAX_LIFETIME + 1,
        ):
            assert not mgr.is_valid("jti-1")

    def test_expired_token(self):
        """Verify connection expired by JWT."""
        mgr = SSEManager()
        past_exp = time.time() - 1
        mgr.connect("jti-1", past_exp)
        assert not mgr.is_valid("jti-1")

    def test_nonexistent_invalid(self):
        """Verify nonexistent connection is invalid."""
        mgr = SSEManager()
        assert not mgr.is_valid("none")

    def test_prune_on_connect(self):
        """Verify expired connections pruned on connect."""
        mgr = SSEManager()
        past_exp = time.time() - 1
        mgr.connect("old", past_exp)
        mgr.connect("new", time.time() + 900)
        assert mgr.active_count == 1


class TestSSESessionLimit:
    """Tests for SSE + SessionManager integration."""

    def test_rejects_when_session_limit_exceeded(self):
        """Verify connect returns None at session limit."""
        sse = SSEManager()
        sm = SessionManager()
        exp = time.time() + 900
        for i in range(MAX_SESSIONS):
            sm.register_session(f"jti-{i}", exp)
        result = sse.connect("extra", exp, session_manager=sm)
        assert result is None

    def test_accepts_within_session_limit(self):
        """Verify connect works within session limit."""
        sse = SSEManager()
        sm = SessionManager()
        exp = time.time() + 900
        result = sse.connect("jti-1", exp, session_manager=sm)
        assert isinstance(result, SSEConnection)
        assert sm.active_session_count == 1

    def test_works_without_session_manager(self):
        """Verify connect works when no session manager."""
        sse = SSEManager()
        exp = time.time() + 900
        result = sse.connect("jti-1", exp)
        assert isinstance(result, SSEConnection)
