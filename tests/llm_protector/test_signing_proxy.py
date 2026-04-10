"""Tests for the signing proxy process.

Covers sign/verify round-trip, disallowed operation rejection,
and allowlist enforcement. Uses direct function calls, not IPC.
"""

from backend.processes.signing_proxy import (
    SIGNING_OPERATIONS,
    _decode_jwt,
    _encode_jwt,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


class TestSigningOperations:
    """Tests for JWT encode/decode functions in Signing Proxy."""

    def _make_key(self) -> Ed25519PrivateKey:
        """Generate a test Ed25519 key."""
        return Ed25519PrivateKey.generate()

    def test_sign_verify_roundtrip(self) -> None:
        """Sign a token and verify it returns the original claims."""
        key = self._make_key()
        claims = {
            "jti": "test-jti-1",
            "iat": 1000000,
            "exp": 9999999999,
            "gen": 1,
            "type": "access",
        }
        token = _encode_jwt(claims, key)
        decoded = _decode_jwt(token, key)

        assert decoded is not None
        assert decoded["jti"] == "test-jti-1"
        assert decoded["iat"] == 1000000
        assert decoded["exp"] == 9999999999
        assert decoded["gen"] == 1
        assert decoded["type"] == "access"

    def test_verify_invalid_token(self) -> None:
        """Invalid token returns None."""
        key = self._make_key()
        assert _decode_jwt("not.a.token", key) is None

    def test_verify_wrong_key(self) -> None:
        """Token signed with different key returns None."""
        key1 = self._make_key()
        key2 = self._make_key()
        claims = {"jti": "x", "iat": 0, "exp": 0, "gen": 0, "type": "access"}
        token = _encode_jwt(claims, key1)
        assert _decode_jwt(token, key2) is None

    def test_verify_malformed_parts(self) -> None:
        """Token with wrong number of parts returns None."""
        key = self._make_key()
        assert _decode_jwt("only.two", key) is None
        assert _decode_jwt("", key) is None

    def test_sign_produces_three_part_jwt(self) -> None:
        """Signed token has header.payload.signature format."""
        key = self._make_key()
        claims = {"jti": "x", "iat": 0, "exp": 0, "gen": 0, "type": "access"}
        token = _encode_jwt(claims, key)
        parts = token.split(".")
        assert len(parts) == 3


class TestSigningAllowlist:
    """Tests for the Signing Proxy allowlist."""

    def test_allowed_operations(self) -> None:
        """sign_token and verify_token are allowed."""
        assert "sign_token" in SIGNING_OPERATIONS
        assert "verify_token" in SIGNING_OPERATIONS

    def test_disallowed_operations(self) -> None:
        """Other operations are not in the allowlist."""
        assert "get_signing_material" not in SIGNING_OPERATIONS
        assert "save_message" not in SIGNING_OPERATIONS
        assert "create_backup" not in SIGNING_OPERATIONS
        assert "store_credential" not in SIGNING_OPERATIONS

    def test_allowlist_size(self) -> None:
        """Exactly 6 operations allowed (JWT + auth)."""
        assert len(SIGNING_OPERATIONS) == 6
