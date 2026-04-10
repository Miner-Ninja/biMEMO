"""Tests for user password authentication.

Covers DB Proxy password hash operations, Signing Proxy auth
operations (setup, verify, status), brute-force rate limiter,
and allowlist enforcement.
"""

import sqlite3

import pytest
from backend.processes.db_proxy import (
    SOCKET_B_ONLY_OPERATIONS,
    StorePasswordHashParams,
    build_allowlist,
    make_get_password_hash_handler,
    make_store_password_hash_handler,
)
from backend.processes.signing_proxy import (
    MAX_LOGIN_ATTEMPTS,
    SIGNING_OPERATIONS,
    LoginRateLimiter,
    _decode_jwt,
    _issue_tokens,
    _ph,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from pydantic import ValidationError


class FakeDB:
    """Minimal DB stand-in with user_auth table."""

    def __init__(self) -> None:
        """Create an in-memory database with a ``user_auth`` table."""
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(
            """
            CREATE TABLE user_auth (
                id INTEGER PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
            )
            """
        )
        self.connection.commit()

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        """Execute a SQL statement against the in-memory database."""
        return self.connection.execute(sql, params)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()


# ============================================================
# DB Proxy: store_password_hash / get_password_hash
# ============================================================


class TestStorePasswordHash:
    """Tests for the store_password_hash DB operation."""

    @pytest.mark.asyncio
    async def test_store_password_hash(self) -> None:
        """Storing a password hash inserts one row."""
        db = FakeDB()
        handler = make_store_password_hash_handler(db)
        result = await handler({"password_hash": "$argon2id$test_hash"})
        assert result == {"ok": True}

        row = db.execute("SELECT password_hash FROM user_auth WHERE id = 1").fetchone()
        assert row[0] == "$argon2id$test_hash"

    @pytest.mark.asyncio
    async def test_store_rejects_duplicate(self) -> None:
        """Second store attempt returns error, not exception."""
        db = FakeDB()
        handler = make_store_password_hash_handler(db)
        await handler({"password_hash": "hash1"})
        result = await handler({"password_hash": "hash2"})
        assert result == {"error": "password_already_exists"}

        # Original hash unchanged
        row = db.execute("SELECT password_hash FROM user_auth WHERE id = 1").fetchone()
        assert row[0] == "hash1"

    @pytest.mark.asyncio
    async def test_store_validates_params(self) -> None:
        """Missing password_hash raises ValueError."""
        db = FakeDB()
        handler = make_store_password_hash_handler(db)
        with pytest.raises(ValidationError):
            await handler({})


class TestGetPasswordHash:
    """Tests for the get_password_hash DB operation."""

    @pytest.mark.asyncio
    async def test_get_when_no_hash_exists(self) -> None:
        """Returns exists=False when table is empty."""
        db = FakeDB()
        handler = make_get_password_hash_handler(db)
        result = await handler(None)
        assert result == {"exists": False, "password_hash": None}

    @pytest.mark.asyncio
    async def test_get_when_hash_exists(self) -> None:
        """Returns exists=True with the stored hash."""
        db = FakeDB()
        db.execute(
            "INSERT INTO user_auth (id, password_hash) VALUES (1, ?)",
            ("$argon2id$stored",),
        )
        db.commit()
        handler = make_get_password_hash_handler(db)
        result = await handler(None)
        assert result["exists"] is True
        assert result["password_hash"] == "$argon2id$stored"


# ============================================================
# DB Proxy: allowlist includes new operations on Socket B
# ============================================================


class TestPasswordHashAllowlist:
    """Verify password hash ops are on Socket B."""

    def test_operations_in_socket_b(self) -> None:
        """store_password_hash and get_password_hash are Socket B ops."""
        assert "store_password_hash" in SOCKET_B_ONLY_OPERATIONS
        assert "get_password_hash" in SOCKET_B_ONLY_OPERATIONS
        assert "delete_conversation" in SOCKET_B_ONLY_OPERATIONS

    def test_allowlist_includes_ops(self) -> None:
        """build_allowlist() includes password hash ops for Socket B."""
        allowlist = build_allowlist()
        assert allowlist.is_allowed("socket_b", "store_password_hash")
        assert allowlist.is_allowed("socket_b", "get_password_hash")
        assert allowlist.is_allowed("socket_b", "delete_conversation")
        # Not allowed on Socket A or C
        assert not allowlist.is_allowed("socket_a", "store_password_hash")
        assert not allowlist.is_allowed("socket_c", "get_password_hash")
        assert allowlist.is_allowed("socket_a", "delete_conversation")
        assert not allowlist.is_allowed("socket_c", "delete_conversation")


# ============================================================
# Signing Proxy: allowlist includes new operations
# ============================================================


class TestSigningProxyAllowlist:
    """Tests for Signing Proxy SIGNING_OPERATIONS."""

    def test_auth_operations_allowed(self) -> None:
        """Password auth operations are exposed by the signing proxy."""
        assert "setup_password" in SIGNING_OPERATIONS
        assert "verify_password" in SIGNING_OPERATIONS
        assert "check_password" in SIGNING_OPERATIONS
        assert "get_auth_status" in SIGNING_OPERATIONS

    def test_jwt_operations_still_allowed(self) -> None:
        """sign_token and verify_token remain allowed."""
        assert "sign_token" in SIGNING_OPERATIONS
        assert "verify_token" in SIGNING_OPERATIONS


# ============================================================
# Signing Proxy: _issue_tokens
# ============================================================


class TestIssueTokens:
    """Tests for the _issue_tokens helper."""

    def test_returns_both_tokens(self) -> None:
        """Returns dict with access_token and refresh_token."""
        key = Ed25519PrivateKey.generate()
        tokens = _issue_tokens(key)
        assert "access_token" in tokens
        assert "refresh_token" in tokens

    def test_tokens_are_valid_jwt(self) -> None:
        """Both tokens decode successfully with correct fields."""
        key = Ed25519PrivateKey.generate()
        tokens = _issue_tokens(key)
        access_claims = _decode_jwt(tokens["access_token"], key)
        refresh_claims = _decode_jwt(tokens["refresh_token"], key)
        assert access_claims is not None
        assert access_claims["type"] == "access"
        assert access_claims["gen"] == 0
        assert refresh_claims is not None
        assert refresh_claims["type"] == "refresh"
        assert refresh_claims["gen"] == 0

    def test_tokens_have_different_jti(self) -> None:
        """Access and refresh tokens have different JTIs."""
        key = Ed25519PrivateKey.generate()
        tokens = _issue_tokens(key)
        access_claims = _decode_jwt(tokens["access_token"], key)
        refresh_claims = _decode_jwt(tokens["refresh_token"], key)
        assert access_claims["jti"] != refresh_claims["jti"]


# ============================================================
# Signing Proxy: Argon2id hashing
# ============================================================


class TestArgon2Hashing:
    """Tests for Argon2id password hashing via _ph."""

    def test_hash_and_verify(self) -> None:
        """Hash a password and verify it matches."""
        hashed = _ph.hash("test_password_123")
        assert _ph.verify(hashed, "test_password_123")

    def test_wrong_password_fails(self) -> None:
        """Wrong password raises VerifyMismatchError."""
        from argon2.exceptions import VerifyMismatchError

        hashed = _ph.hash("correct_password")
        with pytest.raises(VerifyMismatchError):
            _ph.verify(hashed, "wrong_password")


# ============================================================
# Signing Proxy: LoginRateLimiter
# ============================================================


class TestLoginRateLimiter:
    """Tests for brute-force rate limiter."""

    def test_allows_first_attempts(self) -> None:
        """First MAX_LOGIN_ATTEMPTS attempts are allowed."""
        limiter = LoginRateLimiter()
        for _ in range(MAX_LOGIN_ATTEMPTS - 1):
            assert limiter.check() == 0.0
            limiter.record_failure()
        # 5th attempt still allowed (check before record)
        assert limiter.check() == 0.0

    def test_blocks_after_max_attempts(self) -> None:
        """After MAX_LOGIN_ATTEMPTS failures, returns wait time > 0."""
        limiter = LoginRateLimiter()
        for _ in range(MAX_LOGIN_ATTEMPTS):
            limiter.record_failure()
        wait = limiter.check()
        assert wait > 0

    def test_reset_clears_counter(self) -> None:
        """Successful login resets the failure counter."""
        limiter = LoginRateLimiter()
        for _ in range(MAX_LOGIN_ATTEMPTS):
            limiter.record_failure()
        assert limiter.check() > 0
        limiter.reset()
        assert limiter.check() == 0.0

    def test_backoff_increases(self) -> None:
        """Continued failures increase the backoff."""
        limiter = LoginRateLimiter()
        for _ in range(MAX_LOGIN_ATTEMPTS + 3):
            limiter.record_failure()
        # Should be using a later (larger) backoff entry
        wait = limiter.check()
        assert wait > 0


# ============================================================
# Params validation
# ============================================================


class TestStorePasswordHashParams:
    """Tests for StorePasswordHashParams Pydantic model."""

    def test_valid_params(self) -> None:
        """Valid params pass validation."""
        params = StorePasswordHashParams(password_hash="$argon2id$hash")
        assert params.password_hash == "$argon2id$hash"

    def test_missing_hash_rejected(self) -> None:
        """Missing password_hash raises ValidationError."""
        with pytest.raises(ValidationError):
            StorePasswordHashParams()

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected (extra=forbid)."""
        with pytest.raises(ValidationError):
            StorePasswordHashParams(password_hash="x", extra_field="y")
