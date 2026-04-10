"""Data models for JWT authentication.

Defines the TokenClaims dataclass used throughout the JWT
authentication pipeline to represent decoded token claim sets.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenClaims:
    """JWT token claims.

    Attributes:
        jti: Unique token identifier.
        iat: Issued-at timestamp.
        exp: Expiration timestamp.
        generation: Session generation counter.
        token_type: access or refresh.
    """

    jti: str
    iat: float
    exp: float
    generation: int
    token_type: str
