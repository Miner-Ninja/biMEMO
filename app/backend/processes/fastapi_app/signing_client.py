"""Signing Proxy IPC client for JWT and authentication operations.

Delegates JWT signing, verification, and password authentication
to the Signing Proxy process via IPC. Handles token creation,
validation, password setup, and password verification through
the DB Proxy channel infrastructure.
"""

from __future__ import annotations

from backend.processes.fastapi_app.base_client import _BaseIPCClient


class SigningClient(_BaseIPCClient):
    """Typed async client for Signing Proxy (JWT + auth operations).

    Delegates JWT signing, verification, and password auth
    to the Signing Proxy process.
    """

    async def sign_token(self, claims: dict) -> str:
        """Sign a JWT token with the given claims.

        Args:
            claims: Token claims dict (jti, iat, exp, gen, type).

        Returns:
            Signed JWT string.
        """
        result = await self.send_operation("sign_token", {"claims": claims})
        return result["token"]

    async def verify_token(self, token: str) -> dict | None:
        """Verify a JWT token and return claims.

        Args:
            token: Signed JWT string.

        Returns:
            Decoded claims dict if valid, None if invalid.
        """
        try:
            result = await self.send_operation("verify_token", {"token": token})
            return result.get("claims")
        except Exception:
            return None

    async def get_auth_status(self) -> dict:
        """Check if a password has been configured.

        Returns:
            Dict with needs_setup boolean.
        """
        return await self.send_operation("get_auth_status")

    async def setup_password(self, password: str) -> dict:
        """Hash and store a new password, return JWT tokens.

        Args:
            password: Plaintext password from user.

        Returns:
            Dict with access_token and refresh_token.
        """
        return await self.send_operation(
            "setup_password",
            {"password": password},
        )

    async def verify_password(self, password: str) -> dict:
        """Verify password and return JWT tokens on success.

        Args:
            password: Plaintext password from user.

        Returns:
            Dict with access_token and refresh_token.
        """
        return await self.send_operation(
            "verify_password",
            {"password": password},
            include_error_data=True,
        )

    async def check_password(self, password: str) -> None:
        """Verify password without issuing JWTs.

        Args:
            password: Plaintext password from user.
        """
        await self.send_operation(
            "check_password",
            {"password": password},
            include_error_data=True,
        )
