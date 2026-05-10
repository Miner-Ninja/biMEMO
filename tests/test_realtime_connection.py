"""Tests for realtime connection security.

Covers connection lifecycle, lifetime limits,
and single-connection-per-session enforcement.
"""

import time
from unittest.mock import patch

from backend.channels.jwt_auth import (
    MAX_SESSIONS,
    SessionManager,
)
from backend.channels.realtime import (
    REALTIME_MAX_CONNECTIONS,
    REALTIME_MAX_LIFETIME,
    RealtimeConnection,
    RealtimeConnectionManager,
)


class TestRealtimeConnect:
    """Tests for realtime connection registration."""

    def test_connect_returns_connection(self):
        """Verify connect creates connection."""
        mgr = RealtimeConnectionManager()
        conn = mgr.connect("jti-1", time.time() + 900)
        assert isinstance(conn, RealtimeConnection)
        assert conn.session_jti == "jti-1"

    def test_replaces_same_session(self):
        """Verify new connection replaces old."""
        mgr = RealtimeConnectionManager()
        mgr.connect("jti-1", time.time() + 900)
        mgr.connect("jti-1", time.time() + 900)
        assert mgr.active_count == 1

    def test_rejects_over_max(self):
        """Verify rejects when max connections reached."""
        mgr = RealtimeConnectionManager()
        exp = time.time() + 900
        for i in range(REALTIME_MAX_CONNECTIONS):
            assert mgr.connect(f"jti-{i}", exp) is not None
        assert mgr.connect("extra", exp) is None

    def test_accepts_after_disconnect(self):
        """Verify accepts after disconnect frees slot."""
        mgr = RealtimeConnectionManager()
        exp = time.time() + 900
        for i in range(REALTIME_MAX_CONNECTIONS):
            mgr.connect(f"jti-{i}", exp)
        mgr.disconnect("jti-0")
        assert mgr.connect("new", exp) is not None


class TestRealtimeDisconnect:
    """Tests for realtime disconnection."""

    def test_disconnect_existing(self):
        """Verify disconnect removes connection."""
        mgr = RealtimeConnectionManager()
        mgr.connect("jti-1", time.time() + 900)
        assert mgr.disconnect("jti-1")
        assert mgr.active_count == 0

    def test_disconnect_nonexistent(self):
        """Verify disconnect on missing returns False."""
        mgr = RealtimeConnectionManager()
        assert not mgr.disconnect("none")


class TestRealtimeValidity:
    """Tests for realtime connection validity checks."""

    def test_valid_connection(self):
        """Verify fresh connection is valid."""
        mgr = RealtimeConnectionManager()
        mgr.connect("jti-1", time.time() + 900)
        assert mgr.is_valid("jti-1")

    def test_expired_lifetime(self):
        """Verify connection expired by lifetime."""
        mgr = RealtimeConnectionManager()
        mgr.connect("jti-1", time.time() + 900)
        with patch(
            "backend.channels.realtime.time.time",
            return_value=time.time() + REALTIME_MAX_LIFETIME + 1,
        ):
            assert not mgr.is_valid("jti-1")

    def test_expired_token(self):
        """Verify connection expired by JWT."""
        mgr = RealtimeConnectionManager()
        past_exp = time.time() - 1
        mgr.connect("jti-1", past_exp)
        assert not mgr.is_valid("jti-1")

    def test_nonexistent_invalid(self):
        """Verify nonexistent connection is invalid."""
        mgr = RealtimeConnectionManager()
        assert not mgr.is_valid("none")

    def test_prune_on_connect(self):
        """Verify expired connections pruned on connect."""
        mgr = RealtimeConnectionManager()
        past_exp = time.time() - 1
        mgr.connect("old", past_exp)
        mgr.connect("new", time.time() + 900)
        assert mgr.active_count == 1


class TestRealtimeSessionLimit:
    """Tests for realtime + SessionManager integration."""

    def test_rejects_when_session_limit_exceeded(self):
        """Verify connect returns None at session limit."""
        realtime = RealtimeConnectionManager()
        sm = SessionManager()
        exp = time.time() + 900
        for i in range(MAX_SESSIONS):
            sm.register_session(f"jti-{i}", exp)
        result = realtime.connect("extra", exp, session_manager=sm)
        assert result is None

    def test_accepts_within_session_limit(self):
        """Verify connect works within session limit."""
        realtime = RealtimeConnectionManager()
        sm = SessionManager()
        exp = time.time() + 900
        result = realtime.connect("jti-1", exp, session_manager=sm)
        assert isinstance(result, RealtimeConnection)
        assert sm.active_session_count == 1

    def test_works_without_session_manager(self):
        """Verify connect works when no session manager."""
        realtime = RealtimeConnectionManager()
        exp = time.time() + 900
        result = realtime.connect("jti-1", exp)
        assert isinstance(result, RealtimeConnection)
