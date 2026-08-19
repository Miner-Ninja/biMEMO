"""REST endpoints for Web UI channel.

Implements POST /stop, POST /confirm,
GET /tools/status, and re-authentication.
"""

import logging
import secrets
import time
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

# Re-authentication token lifetime
REAUTH_TOKEN_LIFETIME = 300  # 5 minutes

# Rate limits per endpoint (requests per minute)
RATE_LIMITS = {
    "chat": 100,
    "tools_status": 60,
    "stop": 10,
    "confirm": 10,
    "tts": 30,
}

# Backup download rate limit
BACKUP_RATE_LIMIT = 3  # per hour
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


def _now() -> float:
    """Return the current wall-clock timestamp."""
    return time.time()


def _timestamps_in_window(
    timestamps: list[float],
    *,
    now: float,
    window_seconds: int,
) -> list[float]:
    """Return timestamps that still belong to one active rate-limit window."""
    window_start = now - window_seconds
    return [stamp for stamp in timestamps if stamp > window_start]


@dataclass(frozen=True)
class ReauthToken:
    """Short-lived re-authentication token.

    Attributes:
        token: Random token string.
        created_at: Creation timestamp.
        used: Whether the token has been used.
    """

    token: str
    created_at: float
    used: bool = False


@dataclass
class ReauthManager:
    """Manages re-authentication for sensitive ops.

    Issues single-use short-lived tokens after
    password re-entry.
    """

    _tokens: dict[str, ReauthToken] = field(default_factory=dict)

    @staticmethod
    def _is_expired(token: ReauthToken, *, now: float) -> bool:
        """Return whether one re-authentication token is expired."""
        return now - token.created_at > REAUTH_TOKEN_LIFETIME

    def issue_token(self) -> str:
        """Issue a new re-authentication token.

        Returns:
            Token string for use in subsequent request.
        """
        self._prune_expired()
        token_str = secrets.token_urlsafe(32)
        self._tokens[token_str] = ReauthToken(
            token=token_str,
            created_at=_now(),
        )
        return token_str

    def validate_and_consume(self, token: str) -> bool:
        """Validate and consume a re-auth token.

        Single-use: consumed on first validation.

        Args:
            token: Token string to validate.

        Returns:
            True if token is valid and unused.
        """
        self._prune_expired()
        stored = self._tokens.get(token)
        if stored is None:
            return False
        if stored.used:
            return False
        now = _now()
        if self._is_expired(stored, now=now):
            del self._tokens[token]
            return False
        self._tokens[token] = ReauthToken(
            token=stored.token,
            created_at=stored.created_at,
            used=True,
        )
        return True

    def _prune_expired(self) -> None:
        """Remove expired tokens."""
        now = _now()
        expired = [t for t, rt in self._tokens.items() if self._is_expired(rt, now=now)]
        for t in expired:
            del self._tokens[t]


@dataclass
class EndpointRateLimiter:
    """Per-endpoint rate limiter.

    Tracks request timestamps per endpoint and
    enforces configurable limits.
    """

    _requests: dict[str, list[float]] = field(default_factory=dict)

    def limit_for(self, endpoint: str) -> int:
        """Return the configured per-minute limit for one endpoint."""
        return RATE_LIMITS.get(endpoint, SECONDS_PER_MINUTE)

    def _recent_requests(self, endpoint: str) -> list[float]:
        """Return and store only the timestamps still inside the active window."""
        now = _now()
        timestamps = _timestamps_in_window(
            self._requests.get(endpoint, []),
            now=now,
            window_seconds=SECONDS_PER_MINUTE,
        )
        self._requests[endpoint] = timestamps
        return timestamps

    def is_allowed(self, endpoint: str) -> bool:
        """Check if a request to endpoint is allowed.

        Args:
            endpoint: Endpoint name (e.g., "chat").

        Returns:
            True if within rate limit.
        """
        limit = self.limit_for(endpoint)
        timestamps = self._recent_requests(endpoint)
        return len(timestamps) < limit

    def record(self, endpoint: str) -> None:
        """Record a request to an endpoint.

        Args:
            endpoint: Endpoint name.
        """
        self._requests.setdefault(endpoint, []).append(_now())

    def retry_after(self, endpoint: str) -> int:
        """Calculate seconds until next allowed request.

        Args:
            endpoint: Endpoint name.

        Returns:
            Seconds to wait (0 if allowed now).
        """
        limit = self.limit_for(endpoint)
        timestamps = self._recent_requests(endpoint)
        now = _now()
        if len(timestamps) < limit:
            return 0
        oldest = min(timestamps)
        return int(oldest + SECONDS_PER_MINUTE - now) + 1

    def remaining(self, endpoint: str) -> int:
        """Return how many requests are still allowed in the active window.

        Args:
            endpoint: Endpoint name (e.g., "chat").

        Returns:
            Requests left before the per-minute limit (0 when exhausted).
        """
        return max(0, self.limit_for(endpoint) - len(self._recent_requests(endpoint)))

    def reset_seconds(self, endpoint: str) -> int:
        """Return whole seconds until the oldest tracked request leaves the window.

        Args:
            endpoint: Endpoint name (e.g., "chat").

        Returns:
            Seconds until the 60-second window's oldest entry expires;
            0 when the window is empty.
        """
        timestamps = self._recent_requests(endpoint)
        if not timestamps:
            return 0
        return max(0, int(SECONDS_PER_MINUTE - (_now() - min(timestamps))))


@dataclass
class BackupRateLimiter:
    """Rate limiter for backup downloads.

    Enforces 3 downloads per hour maximum.
    """

    _downloads: list[float] = field(default_factory=list)

    def _recent_downloads(self) -> list[float]:
        """Return and store only the downloads still inside the hourly window."""
        now = _now()
        self._downloads = _timestamps_in_window(
            self._downloads,
            now=now,
            window_seconds=SECONDS_PER_HOUR,
        )
        return self._downloads

    def is_allowed(self) -> bool:
        """Check if a download is allowed.

        Returns:
            True if within rate limit.
        """
        return len(self._recent_downloads()) < BACKUP_RATE_LIMIT

    def record(self) -> None:
        """Record a download."""
        self._downloads.append(_now())

    def retry_after(self) -> int:
        """Return whole seconds until the oldest download leaves the hourly window.

        Returns:
            Seconds to wait before another backup download is allowed;
            0 when the window is empty.
        """
        downloads = self._recent_downloads()
        if not downloads:
            return 0
        return max(0, int(SECONDS_PER_HOUR - (_now() - min(downloads))))
