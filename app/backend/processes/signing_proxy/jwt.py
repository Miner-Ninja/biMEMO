"""JWT encoding, decoding, and token issuance."""

import base64
import json
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


JWT_CONTEXT_LABEL = b"bimemo-jwt-signing-v1"

# JWT token lifetimes (seconds).
ACCESS_TOKEN_LIFETIME = 900  # 15 minutes
REFRESH_TOKEN_LIFETIME = 604800  # 7 days
ACCESS_TOKEN_TYPE = "access"  # noqa: S105
REFRESH_TOKEN_TYPE = "refresh"  # noqa: S105


def _urlsafe_b64encode(data: bytes) -> bytes:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _urlsafe_b64decode(data: str) -> bytes:
    """Decode URL-safe base64, restoring any missing padding."""
    padding = 4 - len(data) % 4
    return base64.urlsafe_b64decode(data + "=" * (padding % 4))


def _json_b64(data: dict) -> bytes:
    """Encode one JSON object as URL-safe base64."""
    return _urlsafe_b64encode(json.dumps(data).encode())


def _signature_b64(signature: bytes) -> bytes:
    """Encode one signature as URL-safe base64 without padding."""
    return _urlsafe_b64encode(signature)


def _signing_input(header: bytes, payload: bytes) -> bytes:
    """Build the signed JWT input."""
    return header + b"." + payload


def _split_jwt(token: str) -> tuple[str, str, str] | None:
    """Split one JWT string into its three segments."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _token_claims(
    *,
    now: float,
    lifetime: int,
    token_type: str,
) -> dict:
    """Build one standard token claims payload."""
    return {
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + lifetime,
        "gen": 0,
        "type": token_type,
    }


def _encode_jwt(claims: dict, private_key: Ed25519PrivateKey) -> str:
    """Create and sign a JWT from claims dict.

    Args:
        claims: Token claims (jti, iat, exp, gen, type).
        private_key: Ed25519 signing key.

    Returns:
        Signed JWT string (header.payload.signature).
    """
    header = _json_b64({"alg": "EdDSA", "typ": "JWT"})
    payload = _json_b64(claims)

    signing_input = _signing_input(header, payload)
    signature = private_key.sign(signing_input)
    sig_b64 = _signature_b64(signature)

    return header.decode() + "." + payload.decode() + "." + sig_b64.decode()


def _decode_jwt(token: str, private_key: Ed25519PrivateKey) -> dict | None:
    """Verify and decode a JWT.

    Args:
        token: Signed JWT string.
        private_key: Key for verification (public key derived from it).

    Returns:
        Decoded claims dict if valid, None otherwise.
    """
    parts = _split_jwt(token)
    if parts is None:
        return None

    try:
        header, payload, signature = parts
        signing_input = _signing_input(header.encode(), payload.encode())
        sig_bytes = _urlsafe_b64decode(signature)
        public_key = private_key.public_key()
        public_key.verify(sig_bytes, signing_input)

        payload_bytes = _urlsafe_b64decode(payload)
        return json.loads(payload_bytes)
    except Exception:
        return None


def _issue_tokens(private_key: Ed25519PrivateKey) -> dict:
    """Issue access and refresh JWT tokens.

    Args:
        private_key: Ed25519 signing key.

    Returns:
        Dict with access_token and refresh_token strings.
    """
    now = time.time()
    access_claims = _token_claims(
        now=now,
        lifetime=ACCESS_TOKEN_LIFETIME,
        token_type=ACCESS_TOKEN_TYPE,
    )
    refresh_claims = _token_claims(
        now=now,
        lifetime=REFRESH_TOKEN_LIFETIME,
        token_type=REFRESH_TOKEN_TYPE,
    )
    return {
        "access_token": _encode_jwt(access_claims, private_key),
        "refresh_token": _encode_jwt(refresh_claims, private_key),
    }
