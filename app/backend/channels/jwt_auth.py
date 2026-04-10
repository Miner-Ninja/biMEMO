"""JWT authentication for Web UI.

Implements EdDSA (Ed25519) token signing via HKDF-SHA256
derived key, with access/refresh token lifecycle.
"""

import hmac
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.channels.jwt_auth_models import TokenClaims


logger = logging.getLogger(__name__)

# Token lifetimes in seconds
ACCESS_TOKEN_LIFETIME = 900  # 15 minutes
REFRESH_TOKEN_LIFETIME = 604800  # 7 days
CSRF_TOKEN_LENGTH = 32
JWT_CONTEXT_LABEL = b"bimemo-jwt-signing-v1"

# Global authenticated session limit
MAX_SESSIONS = 5


def _urlsafe_b64encode_json(data: dict[str, object]) -> bytes:
    """Encode one JSON payload as unpadded URL-safe base64."""
    import base64

    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")


def _urlsafe_b64decode_json(data: str) -> dict[str, object]:
    """Decode one unpadded URL-safe base64 JSON payload."""
    import base64

    padding = 4 - len(data) % 4
    payload_bytes = base64.urlsafe_b64decode(data + "=" * (padding % 4))
    payload = json.loads(payload_bytes)
    if not isinstance(payload, dict):
        raise TypeError("JWT payload must decode into a JSON object")
    return payload


def _urlsafe_b64decode_bytes(data: str) -> bytes:
    """Decode one unpadded URL-safe base64 payload into bytes."""
    import base64

    padding = 4 - len(data) % 4
    return base64.urlsafe_b64decode(data + "=" * (padding % 4))


def _urlsafe_b64encode_bytes(data: bytes) -> bytes:
    """Encode raw bytes as unpadded URL-safe base64."""
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=")


def get_or_create_jwt_salt(db: object) -> bytes:
    """Get or create the JWT HKDF salt from the database.

    On first unlock, generates 16 random bytes and stores them
    in the metadata table. On subsequent unlocks, returns the
    existing salt for deterministic key derivation.

    Args:
        db: DatabaseManager instance with an open connection.

    Returns:
        16-byte salt for HKDF derivation.
    """
    import os

    row = db.execute("SELECT value FROM metadata WHERE key = 'jwt_salt'").fetchone()
    if row is not None:
        return row[0]
    salt = os.urandom(16)
    db.execute(
        "INSERT INTO metadata (key, value) VALUES ('jwt_salt', ?)",
        (salt,),
    )
    db.commit()
    return salt


def derive_signing_key(master_key: bytes, salt: bytes) -> Ed25519PrivateKey:
    """Derive Ed25519 signing key from master key.

    Uses HKDF-SHA256 with context label to derive
    32 bytes of key material for Ed25519.

    Args:
        master_key: 256-bit master key.
        salt: 16-byte random salt.

    Returns:
        Ed25519 private key for signing.
    """
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=salt,
        info=JWT_CONTEXT_LABEL,
    )
    key_material = hkdf.derive(master_key)
    return Ed25519PrivateKey.from_private_bytes(key_material)


def encode_token(
    claims: TokenClaims,
    private_key: Ed25519PrivateKey,
) -> str:
    """Encode and sign a JWT token.

    Args:
        claims: Token claims to encode.
        private_key: Ed25519 signing key.

    Returns:
        Signed JWT string (header.payload.signature).
    """
    header = _urlsafe_b64encode_json({"alg": "EdDSA", "typ": "JWT"})
    payload = _urlsafe_b64encode_json(_claims_payload(claims))

    signing_input = header + b"." + payload
    signature = private_key.sign(signing_input)
    sig_b64 = _urlsafe_b64encode_bytes(signature)
    return header.decode() + "." + payload.decode() + "." + sig_b64.decode()


def decode_token(
    token: str,
    private_key: Ed25519PrivateKey,
) -> TokenClaims | None:
    """Decode and verify a JWT token.

    Args:
        token: Signed JWT string.
        private_key: Key for verification.

    Returns:
        TokenClaims if valid, None otherwise.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None

    try:
        signing_input = parts[0].encode() + b"." + parts[1].encode()
        sig_bytes = _urlsafe_b64decode_bytes(parts[2])
        public_key = private_key.public_key()
        public_key.verify(sig_bytes, signing_input)
        return _claims_from_payload(_urlsafe_b64decode_json(parts[1]))
    except Exception:
        return None


def generate_csrf_token() -> str:
    """Generate a CSRF token.

    Returns:
        Random URL-safe string.
    """
    return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)


def verify_csrf(token: str, expected: str) -> bool:
    """Verify CSRF token with constant-time compare.

    Args:
        token: Token from request.
        expected: Expected token from session.

    Returns:
        True if tokens match.
    """
    return hmac.compare_digest(token, expected)


def _issued_token_claims(
    *,
    generation: int,
    token_type: str,
    lifetime_seconds: float,
) -> TokenClaims:
    """Build one freshly issued token claim set."""
    now = time.time()
    return TokenClaims(
        jti=secrets.token_urlsafe(16),
        iat=now,
        exp=now + lifetime_seconds,
        generation=generation,
        token_type=token_type,
    )


def _issued_token_payload(
    *,
    generation: int,
    token_type: str,
    lifetime_seconds: float,
) -> tuple[TokenClaims, dict[str, object]]:
    """Build one freshly issued signing payload."""
    claims = _issued_token_claims(
        generation=generation,
        token_type=token_type,
        lifetime_seconds=lifetime_seconds,
    )
    return claims, _claims_payload(claims)


def _claims_payload(claims: TokenClaims) -> dict[str, object]:
    """Convert token claims into a signing-proxy payload."""
    return {
        "jti": claims.jti,
        "iat": claims.iat,
        "exp": claims.exp,
        "gen": claims.generation,
        "type": claims.token_type,
    }


def _claims_from_payload(data: object) -> TokenClaims | None:
    """Convert one signing-proxy payload back into TokenClaims."""
    if not isinstance(data, dict):
        return None
    try:
        return TokenClaims(
            jti=data["jti"],
            iat=data["iat"],
            exp=data["exp"],
            generation=data["gen"],
            token_type=data["type"],
        )
    except (KeyError, TypeError):
        return None


@dataclass
class SessionManager:
    """Manages JWT sessions and generation counter.

    Attributes:
        generation: Current session generation counter.
    """

    generation: int = 0
    _revoked_jtis: set[str] = field(default_factory=set)
    _jti_expiry: dict[str, float] = field(default_factory=dict)
    _active_sessions: dict[str, float] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def increment_generation(self) -> int:
        """Increment session generation counter.

        Invalidates all tokens with previous generation.

        Returns:
            New generation counter value.
        """
        self.generation += 1
        return self.generation

    def _claims_pass_basic_validation(
        self,
        claims: TokenClaims,
        *,
        expected_type: str,
    ) -> bool:
        """Check expiration, generation, and token type."""
        if claims.exp < time.time():
            return False
        if claims.generation != self.generation:
            return False
        return claims.token_type == expected_type

    def _validated_claims(
        self,
        claims: TokenClaims | None,
        *,
        expected_type: str,
    ) -> TokenClaims | None:
        """Return verified claims only when they pass local checks."""
        if claims is None:
            return None
        if not self._claims_pass_basic_validation(
            claims,
            expected_type=expected_type,
        ):
            return None
        if self._is_revoked(claims.jti):
            return None
        return claims

    def _is_revoked(self, jti: str) -> bool:
        """Return whether one token id is revoked."""
        with self._lock:
            return jti in self._revoked_jtis

    def _expired_jtis(self, expiry_map: dict[str, float]) -> list[str]:
        """Return expired token ids from one expiry map."""
        now = time.time()
        return [jti for jti, exp in expiry_map.items() if exp < now]

    def _drop_active_session(self, jti: str) -> bool:
        """Remove one active session if it exists."""
        if jti not in self._active_sessions:
            return False
        del self._active_sessions[jti]
        return True

    def issue_access_token(
        self,
        private_key: Ed25519PrivateKey,
    ) -> str:
        """Issue a new access token.

        Args:
            private_key: Ed25519 signing key.

        Returns:
            Signed JWT access token string.
        """
        claims, _ = _issued_token_payload(
            generation=self.generation,
            token_type="access",  # noqa: S106
            lifetime_seconds=ACCESS_TOKEN_LIFETIME,
        )
        return encode_token(claims, private_key)

    def issue_refresh_token(
        self,
        private_key: Ed25519PrivateKey,
    ) -> str:
        """Issue a new refresh token.

        Args:
            private_key: Ed25519 signing key.

        Returns:
            Signed JWT refresh token string.
        """
        claims, _ = _issued_token_payload(
            generation=self.generation,
            token_type="refresh",  # noqa: S106
            lifetime_seconds=REFRESH_TOKEN_LIFETIME,
        )
        return encode_token(claims, private_key)

    def validate_token(
        self,
        token: str,
        private_key: Ed25519PrivateKey,
        expected_type: str = "access",
    ) -> TokenClaims | None:
        """Validate a JWT token.

        Checks signature, expiration, generation,
        revocation, and token type.

        Args:
            token: JWT string to validate.
            private_key: Signing key for verification.
            expected_type: Expected token type.

        Returns:
            TokenClaims if valid, None otherwise.
        """
        claims = decode_token(token, private_key)
        return self._validated_claims(
            claims,
            expected_type=expected_type,
        )

    async def async_issue_access_token(
        self,
        signing_client: object,
    ) -> str:
        """Issue a new access token via Signing Proxy.

        Args:
            signing_client: Object with async sign_token(claims) method.

        Returns:
            Signed JWT access token string.
        """
        _, payload = _issued_token_payload(
            generation=self.generation,
            token_type="access",  # noqa: S106
            lifetime_seconds=ACCESS_TOKEN_LIFETIME,
        )
        return await signing_client.sign_token(payload)

    async def async_issue_refresh_token(
        self,
        signing_client: object,
    ) -> str:
        """Issue a new refresh token via Signing Proxy.

        Args:
            signing_client: Object with async sign_token(claims) method.

        Returns:
            Signed JWT refresh token string.
        """
        _, payload = _issued_token_payload(
            generation=self.generation,
            token_type="refresh",  # noqa: S106
            lifetime_seconds=REFRESH_TOKEN_LIFETIME,
        )
        return await signing_client.sign_token(payload)

    async def async_validate_token(
        self,
        token: str,
        signing_client: object,
        expected_type: str = "access",
        audit_fn: object = None,
    ) -> "TokenClaims | None":
        """Validate a JWT token via Signing Proxy.

        Delegates signature verification to the signing client,
        then checks expiration, generation, revocation, and type.

        Args:
            token: JWT string to validate.
            signing_client: Object with async verify_token(token) method.
            expected_type: Expected token type.
            audit_fn: Optional async callable for token_revoked events.

        Returns:
            TokenClaims if valid, None otherwise.
        """
        data = await signing_client.verify_token(token)
        claims = self._validated_claims(
            _claims_from_payload(data),
            expected_type=expected_type,
        )
        if claims is None:
            if audit_fn is not None:
                payload = _claims_from_payload(data)
                if payload is not None and self._is_revoked(payload.jti):
                    await audit_fn("token_revoked", tool_name=payload.jti[:8])
            return None
        return claims

    def revoke_token(self, jti: str, exp: float) -> None:
        """Revoke a specific token by JTI.

        Args:
            jti: Token identifier to revoke.
            exp: Token expiration for cleanup.
        """
        with self._lock:
            self._revoked_jtis.add(jti)
            self._jti_expiry[jti] = exp

    def prune_revoked(self) -> None:
        """Remove expired entries from revocation list."""
        with self._lock:
            for jti in self._expired_jtis(self._jti_expiry):
                self._revoked_jtis.discard(jti)
                del self._jti_expiry[jti]

    def _prune_expired_sessions(self) -> None:
        """Remove sessions whose JWT has expired."""
        for jti in self._expired_jtis(self._active_sessions):
            del self._active_sessions[jti]

    def register_session(self, jti: str, exp: float) -> bool:
        """Register an active session.

        Prunes expired sessions, then checks limit.

        Args:
            jti: JWT token identifier.
            exp: Token expiration timestamp.

        Returns:
            True if registered, False if at limit.
        """
        self._prune_expired_sessions()
        if len(self._active_sessions) >= MAX_SESSIONS:
            return False
        self._active_sessions[jti] = exp
        return True

    def unregister_session(self, jti: str) -> bool:
        """Remove an active session.

        Args:
            jti: JWT token identifier.

        Returns:
            True if session existed and was removed.
        """
        return self._drop_active_session(jti)

    @property
    def active_session_count(self) -> int:
        """Return number of active sessions."""
        return len(self._active_sessions)
