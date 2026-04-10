"""Tests for IPC HMAC-SHA256 authentication.

Covers message signing, verification, invalid HMAC rejection,
frame size validation, and tamper detection.
"""

import os

import pytest
from backend.ipc.protocol import (
    HMAC_TAG_LEN,
    AuthenticationError,
    sign_message,
    verify_and_extract,
)


@pytest.fixture
def secret():
    """Generate a test shared secret."""
    return os.urandom(32)


class TestSignMessage:
    """Tests for HMAC message signing."""

    def test_sign_produces_frame(self, secret):
        """Verify sign returns length-prefixed frame."""
        body = b'{"request_id": "1"}'
        frame = sign_message(body, secret)
        assert len(frame) == 4 + len(body) + HMAC_TAG_LEN

    def test_sign_different_secrets_different_frames(self):
        """Verify different secrets produce different frames."""
        body = b"test"
        frame_a = sign_message(body, os.urandom(32))
        frame_b = sign_message(body, os.urandom(32))
        assert frame_a != frame_b


class TestVerifyAndExtract:
    """Tests for HMAC verification and extraction."""

    def test_roundtrip(self, secret):
        """Verify sign then verify returns original body."""
        body = b'{"tool": "file_read"}'
        frame = sign_message(body, secret)
        payload = frame[4:]
        extracted = verify_and_extract(payload, secret)
        assert extracted == body

    def test_wrong_secret_rejected(self, secret):
        """Verify wrong secret fails HMAC check."""
        body = b"test"
        frame = sign_message(body, secret)
        payload = frame[4:]
        with pytest.raises(AuthenticationError, match="HMAC"):
            verify_and_extract(payload, os.urandom(32))

    def test_tampered_body_rejected(self, secret):
        """Verify tampered message body fails HMAC check."""
        body = b"original"
        frame = sign_message(body, secret)
        payload = bytearray(frame[4:])
        payload[0] ^= 0xFF
        with pytest.raises(AuthenticationError, match="HMAC"):
            verify_and_extract(bytes(payload), secret)

    def test_tampered_tag_rejected(self, secret):
        """Verify tampered HMAC tag fails check."""
        body = b"test"
        frame = sign_message(body, secret)
        payload = bytearray(frame[4:])
        payload[-1] ^= 0xFF
        with pytest.raises(AuthenticationError, match="HMAC"):
            verify_and_extract(bytes(payload), secret)

    def test_too_short_frame_rejected(self, secret):
        """Verify frame shorter than HMAC tag is rejected."""
        with pytest.raises(AuthenticationError, match="too short"):
            verify_and_extract(b"short", secret)
