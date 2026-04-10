"""Tests for REST endpoints utilities.

Covers re-authentication tokens, endpoint rate
limiting, and backup download rate limiting.
"""

import time
from unittest.mock import patch

from backend.channels.endpoints import (
    BACKUP_RATE_LIMIT,
    REAUTH_TOKEN_LIFETIME,
    BackupRateLimiter,
    EndpointRateLimiter,
    ReauthManager,
)


class TestReauthManager:
    """Tests for re-authentication token management."""

    def test_issue_returns_token(self):
        """Verify token is issued."""
        mgr = ReauthManager()
        token = mgr.issue_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_validate_valid_token(self):
        """Verify valid token passes."""
        mgr = ReauthManager()
        token = mgr.issue_token()
        assert mgr.validate_and_consume(token)

    def test_single_use(self):
        """Verify token is single-use."""
        mgr = ReauthManager()
        token = mgr.issue_token()
        assert mgr.validate_and_consume(token)
        assert not mgr.validate_and_consume(token)

    def test_expired_token(self):
        """Verify expired token fails."""
        mgr = ReauthManager()
        token = mgr.issue_token()
        now = time.time()
        future = now + REAUTH_TOKEN_LIFETIME + 1
        with patch(
            "backend.channels.endpoints.time.time",
            side_effect=[now, future],
        ):
            assert not mgr.validate_and_consume(token)

    def test_unknown_token(self):
        """Verify unknown token fails."""
        mgr = ReauthManager()
        assert not mgr.validate_and_consume("bogus")

    def test_prune_on_issue(self):
        """Verify expired tokens pruned on issue."""
        mgr = ReauthManager()
        mgr.issue_token()
        with patch(
            "backend.channels.endpoints.time.time",
            return_value=time.time() + REAUTH_TOKEN_LIFETIME + 1,
        ):
            mgr.issue_token()
        assert len(mgr._tokens) == 1


class TestEndpointRateLimiter:
    """Tests for per-endpoint rate limiting."""

    def test_allows_within_limit(self):
        """Verify allows requests within limit."""
        limiter = EndpointRateLimiter()
        assert limiter.is_allowed("stop")

    def test_blocks_over_limit(self):
        """Verify blocks when limit exceeded."""
        limiter = EndpointRateLimiter()
        for _ in range(10):
            limiter.record("stop")
        assert not limiter.is_allowed("stop")

    def test_records_request(self):
        """Verify request is recorded."""
        limiter = EndpointRateLimiter()
        limiter.record("chat")
        assert len(limiter._requests["chat"]) == 1

    def test_window_expires(self):
        """Verify old requests expire from window."""
        limiter = EndpointRateLimiter()
        for _ in range(10):
            limiter.record("stop")
        with patch(
            "backend.channels.endpoints.time.time",
            return_value=time.time() + 61,
        ):
            assert limiter.is_allowed("stop")

    def test_retry_after_zero_when_allowed(self):
        """Verify retry_after is 0 when allowed."""
        limiter = EndpointRateLimiter()
        assert limiter.retry_after("chat") == 0

    def test_retry_after_positive_when_blocked(self):
        """Verify retry_after is positive when blocked."""
        limiter = EndpointRateLimiter()
        for _ in range(10):
            limiter.record("stop")
        assert limiter.retry_after("stop") > 0

    def test_default_limit_for_unknown(self):
        """Verify unknown endpoint gets default limit."""
        limiter = EndpointRateLimiter()
        assert limiter.is_allowed("unknown_endpoint")


class TestBackupRateLimiter:
    """Tests for backup download rate limiting."""

    def test_allows_first_download(self):
        """Verify first download allowed."""
        limiter = BackupRateLimiter()
        assert limiter.is_allowed()

    def test_blocks_over_limit(self):
        """Verify blocks after limit exceeded."""
        limiter = BackupRateLimiter()
        for _ in range(BACKUP_RATE_LIMIT):
            limiter.record()
        assert not limiter.is_allowed()

    def test_window_expires(self):
        """Verify old downloads expire from window."""
        limiter = BackupRateLimiter()
        for _ in range(BACKUP_RATE_LIMIT):
            limiter.record()
        with patch(
            "backend.channels.endpoints.time.time",
            return_value=time.time() + 3601,
        ):
            assert limiter.is_allowed()
