"""Tests for JWT authentication.

Covers key derivation, token encoding/decoding,
session management, and CSRF verification.
"""

import threading
import time
from unittest.mock import patch

from backend.channels.jwt_auth import (
    ACCESS_TOKEN_LIFETIME,
    MAX_SESSIONS,
    SessionManager,
    TokenClaims,
    decode_token,
    derive_signing_key,
    encode_token,
    generate_csrf_token,
    verify_csrf,
)


class TestDeriveSigningKey:
    """Tests for HKDF key derivation."""

    def test_derives_key(self):
        """Verify key is derived from master key."""
        master = b"\x01" * 32
        salt = b"\x02" * 16
        key = derive_signing_key(master, salt)
        assert key is not None

    def test_different_salt_different_key(self):
        """Verify different salts produce different keys."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
        )

        master = b"\x01" * 32
        key1 = derive_signing_key(master, b"\x02" * 16)
        key2 = derive_signing_key(master, b"\x03" * 16)
        raw1 = key1.private_bytes(
            Encoding.Raw,
            PrivateFormat.Raw,
            NoEncryption(),
        )
        raw2 = key2.private_bytes(
            Encoding.Raw,
            PrivateFormat.Raw,
            NoEncryption(),
        )
        assert raw1 != raw2


class TestTokenEncoding:
    """Tests for JWT token encode/decode."""

    def _get_key(self):
        """Create a test signing key."""
        return derive_signing_key(b"\x01" * 32, b"\x02" * 16)

    def test_encode_decode_roundtrip(self):
        """Verify token roundtrip works."""
        key = self._get_key()
        now = time.time()
        claims = TokenClaims(
            jti="test-jti",
            iat=now,
            exp=now + 900,
            generation=1,
            token_type="access",
        )
        token = encode_token(claims, key)
        decoded = decode_token(token, key)
        assert decoded is not None
        assert decoded.jti == "test-jti"
        assert decoded.generation == 1
        assert decoded.token_type == "access"

    def test_tampered_token_rejected(self):
        """Verify tampered token fails verification."""
        key = self._get_key()
        now = time.time()
        claims = TokenClaims(
            jti="x",
            iat=now,
            exp=now + 900,
            generation=1,
            token_type="access",
        )
        token = encode_token(claims, key)
        tampered = token[:-3] + "xxx"
        assert decode_token(tampered, key) is None

    def test_invalid_format_rejected(self):
        """Verify invalid token format rejected."""
        key = self._get_key()
        assert decode_token("not.a.token!", key) is None
        assert decode_token("invalid", key) is None

    def test_wrong_key_rejected(self):
        """Verify wrong key fails verification."""
        key1 = derive_signing_key(b"\x01" * 32, b"\x02" * 16)
        key2 = derive_signing_key(b"\x01" * 32, b"\x03" * 16)
        now = time.time()
        claims = TokenClaims(
            jti="x",
            iat=now,
            exp=now + 900,
            generation=1,
            token_type="access",
        )
        token = encode_token(claims, key1)
        assert decode_token(token, key2) is None


class TestCsrf:
    """Tests for CSRF token handling."""

    def test_generate_returns_string(self):
        """Verify CSRF token is generated."""
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_matching_tokens(self):
        """Verify matching tokens pass."""
        token = generate_csrf_token()
        assert verify_csrf(token, token)

    def test_verify_mismatched_tokens(self):
        """Verify mismatched tokens fail."""
        assert not verify_csrf("abc", "xyz")


class TestSessionManager:
    """Tests for session management."""

    def _get_key(self):
        """Create a test signing key."""
        return derive_signing_key(b"\x01" * 32, b"\x02" * 16)

    def test_issue_access_token(self):
        """Verify access token is issued."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_access_token(key)
        assert isinstance(token, str)

    def test_issue_refresh_token(self):
        """Verify refresh token is issued."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_refresh_token(key)
        claims = decode_token(token, key)
        assert claims is not None
        assert claims.token_type == "refresh"

    def test_validate_valid_token(self):
        """Verify valid token passes validation."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_access_token(key)
        claims = mgr.validate_token(token, key)
        assert claims is not None

    def test_validate_expired_token(self):
        """Verify expired token fails validation."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_access_token(key)
        with patch(
            "backend.channels.jwt_auth.time.time",
            return_value=time.time() + ACCESS_TOKEN_LIFETIME + 1,
        ):
            claims = mgr.validate_token(token, key)
        assert claims is None

    def test_validate_wrong_generation(self):
        """Verify wrong generation fails validation."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_access_token(key)
        mgr.increment_generation()
        claims = mgr.validate_token(token, key)
        assert claims is None

    def test_validate_revoked_token(self):
        """Verify revoked token fails validation."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_access_token(key)
        claims = decode_token(token, key)
        mgr.revoke_token(claims.jti, claims.exp)
        assert mgr.validate_token(token, key) is None

    def test_validate_wrong_type(self):
        """Verify wrong token type fails."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_refresh_token(key)
        claims = mgr.validate_token(token, key, expected_type="access")
        assert claims is None

    def test_increment_generation(self):
        """Verify generation increments."""
        mgr = SessionManager()
        assert mgr.generation == 0
        mgr.increment_generation()
        assert mgr.generation == 1

    def test_prune_revoked(self):
        """Verify expired revocations are pruned."""
        mgr = SessionManager()
        mgr.revoke_token("old-jti", time.time() - 1)
        mgr.prune_revoked()
        assert "old-jti" not in mgr._revoked_jtis

    def test_validate_invalid_token(self):
        """Verify invalid token returns None."""
        mgr = SessionManager()
        key = self._get_key()
        result = mgr.validate_token("garbage", key)
        assert result is None


class TestSessionLimit:
    """Tests for global session limit enforcement."""

    def test_register_up_to_max(self):
        """Verify accepts up to MAX_SESSIONS."""
        mgr = SessionManager()
        exp = time.time() + 900
        for i in range(MAX_SESSIONS):
            assert mgr.register_session(f"jti-{i}", exp)
        assert mgr.active_session_count == MAX_SESSIONS

    def test_rejects_over_max(self):
        """Verify rejects session beyond limit."""
        mgr = SessionManager()
        exp = time.time() + 900
        for i in range(MAX_SESSIONS):
            mgr.register_session(f"jti-{i}", exp)
        assert not mgr.register_session("extra", exp)

    def test_expired_sessions_pruned(self):
        """Verify expired sessions pruned before check."""
        mgr = SessionManager()
        past_exp = time.time() - 1
        for i in range(MAX_SESSIONS):
            mgr._active_sessions[f"old-{i}"] = past_exp
        assert mgr.register_session("new", time.time() + 900)
        assert mgr.active_session_count == 1

    def test_unregister_frees_slot(self):
        """Verify unregister allows new session."""
        mgr = SessionManager()
        exp = time.time() + 900
        for i in range(MAX_SESSIONS):
            mgr.register_session(f"jti-{i}", exp)
        assert mgr.unregister_session("jti-0")
        assert mgr.register_session("new", exp)

    def test_unregister_nonexistent(self):
        """Verify unregister of missing returns False."""
        mgr = SessionManager()
        assert not mgr.unregister_session("nope")

    def test_active_session_count(self):
        """Verify active_session_count property."""
        mgr = SessionManager()
        assert mgr.active_session_count == 0
        mgr.register_session("jti-1", time.time() + 900)
        assert mgr.active_session_count == 1


class TestSessionManagerConcurrency:
    """Tests for thread-safety of SessionManager._lock."""

    def _get_key(self):
        from backend.channels.jwt_auth import derive_signing_key

        return derive_signing_key(b"\x01" * 32, b"\x02" * 16)

    def test_concurrent_revoke_and_validate_no_crash(self):
        """Verify concurrent revoke + validate does not raise or corrupt state."""
        mgr = SessionManager()
        key = self._get_key()
        tokens = [mgr.issue_access_token(key) for _ in range(20)]
        claims_list = [decode_token(t, key) for t in tokens]
        errors: list[Exception] = []

        def revoker():
            for claims in claims_list[:10]:
                try:
                    mgr.revoke_token(claims.jti, claims.exp)
                except Exception as exc:
                    errors.append(exc)

        def validator():
            for t in tokens[10:]:
                try:
                    mgr.validate_token(t, key)
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=revoker),
            threading.Thread(target=validator),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5.0)

        assert not errors, f"Thread errors: {errors}"

    def test_concurrent_revoke_makes_tokens_invalid(self):
        """Verify tokens revoked by one thread are rejected by another."""
        mgr = SessionManager()
        key = self._get_key()
        token = mgr.issue_access_token(key)
        claims = decode_token(token, key)
        revoked_by_thread: list[bool] = []
        validated_after: list[object] = []

        def revoker():
            mgr.revoke_token(claims.jti, claims.exp)
            revoked_by_thread.append(True)

        def validator():
            # Small sleep to let revoker likely run first
            import time as t

            t.sleep(0.01)
            validated_after.append(mgr.validate_token(token, key))

        th_rev = threading.Thread(target=revoker)
        th_val = threading.Thread(target=validator)
        th_rev.start()
        th_val.start()
        th_rev.join(timeout=2.0)
        th_val.join(timeout=2.0)

        assert revoked_by_thread
        # Token was revoked; validation must return None
        assert validated_after[0] is None
