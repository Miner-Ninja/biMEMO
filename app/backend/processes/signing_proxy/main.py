"""Signing Proxy process main loop."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.ipc.ipc_channel import IPCSocketClient, IPCSocketServer
from backend.ipc.models import DBOperation, DBResult

from .jwt import _decode_jwt, _encode_jwt, _issue_tokens


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


logger = logging.getLogger(__name__)

# Argon2id parameters: 64MB memory, 3 iterations, 4 parallelism.
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
)

# Operations this proxy is allowed to handle.
SIGNING_OPERATIONS = frozenset(
    {
        "sign_token",
        "verify_token",
        "setup_password",
        "verify_password",
        "check_password",
        "get_auth_status",
    }
)

# --- Brute-force rate limiter ---
MAX_LOGIN_ATTEMPTS = 5
BACKOFF_SCHEDULE = (30, 60, 120, 300)  # seconds after 5th, 6th, 7th, 8th+ fail
MIN_INITIAL_PASSWORD_LENGTH = 8
ADMIN_ENV_SOURCE_LABEL = "admin-env"


class LoginRateLimiter:
    """In-memory brute-force rate limiter for login attempts."""

    def __init__(self) -> None:
        """Initialize with zero failures."""
        self._fail_count: int = 0
        self._last_fail_time: float = 0.0

    def check(self) -> float:
        """Check if a login attempt is allowed.

        Returns:
            0.0 if allowed, or seconds to wait before next attempt.
        """
        if self._fail_count < MAX_LOGIN_ATTEMPTS:
            return 0.0
        idx = min(
            self._fail_count - MAX_LOGIN_ATTEMPTS,
            len(BACKOFF_SCHEDULE) - 1,
        )
        backoff = BACKOFF_SCHEDULE[idx]
        elapsed = time.monotonic() - self._last_fail_time
        remaining = backoff - elapsed
        return max(0.0, remaining)

    def record_failure(self) -> None:
        """Record a failed login attempt."""
        self._fail_count += 1
        self._last_fail_time = time.monotonic()

    def reset(self) -> None:
        """Reset the failure counter on successful login."""
        self._fail_count = 0
        self._last_fail_time = 0.0


async def _db_send(
    db_client: IPCSocketClient,
    operation: str,
    parameters: dict | None = None,
) -> DBResult:
    """Send a DBOperation to DB Proxy and return the result.

    Args:
        db_client: Connected IPC client to DB Proxy Socket B.
        operation: Operation name.
        parameters: Optional operation parameters.

    Returns:
        DBResult from DB Proxy.
    """
    op = DBOperation(
        request_id=f"signing-{operation}-{uuid.uuid4().hex[:8]}",
        operation=operation,
        parameters=parameters,
    )
    response = await db_client.send_and_receive(op)
    if not isinstance(response, DBResult):
        msg = f"Unexpected response type from DB Proxy: {type(response)}"
        raise RuntimeError(msg)
    return response


async def _connect_signing_db_client(
    db_socket_b_path: str,
    db_secret_b: bytes,
    db_type_map: dict[str, type[DBResult]],
) -> IPCSocketClient:
    """Connect the signing proxy to DB Proxy Socket B."""
    db_client = IPCSocketClient(
        socket_path=db_socket_b_path,
        secret=db_secret_b,
        type_map=db_type_map,
    )
    await db_client.connect()
    return db_client


async def _load_signing_private_key(
    db_client: IPCSocketClient,
) -> Ed25519PrivateKey:
    """Fetch signing material and derive the Ed25519 private key."""
    response = await db_client.send_and_receive(
        DBOperation(
            request_id="signing-proxy-init",
            operation="get_signing_material",
        )
    )
    if not isinstance(response, DBResult) or response.status != "success":
        raise RuntimeError("Failed to get signing material from DB Proxy")
    key_bytes = bytes.fromhex(response.data["key_material"])
    return Ed25519PrivateKey.from_private_bytes(key_bytes)


def _legacy_admin_secret_source() -> object | None:
    """Return the legacy admin secret source when configured."""
    from backend.security.startup_password import StartupPasswordSource

    admin_secret = os.environ.get("BIMEMO_ADMIN_PASSWORD")
    if not admin_secret:
        return None
    return StartupPasswordSource(
        password=admin_secret,
        source=ADMIN_ENV_SOURCE_LABEL,
    )


def _startup_secret_source(startup_password: str | None = None) -> object | None:
    """Return the password-startup secret source when configured."""
    from backend.config import get_settings
    from backend.security.startup_password import (
        StartupPasswordError,
        StartupPasswordSource,
        load_startup_password,
    )

    if startup_password is not None:
        return StartupPasswordSource(
            password=startup_password,
            source="startup-gate",
        )

    settings = get_settings()
    if settings.key_source != "password":
        return None
    try:
        return load_startup_password(
            password_file=settings.unlock_password_file,
            password_env=settings.unlock_password,
        )
    except StartupPasswordError as e:
        logger.error("Failed to load startup password for UI seed: %s", e)
        return None


def _initial_secret_source(startup_password: str | None = None) -> object | None:
    """Return the initial UI password source in priority order."""
    legacy_source = _legacy_admin_secret_source()
    if legacy_source is not None:
        return legacy_source
    return _startup_secret_source(startup_password)


def _verify_existing_password_hash(
    *,
    stored_hash: str,
    secret_source: object,
) -> None:
    """Verify that a startup password source matches the stored UI password."""
    try:
        _ph.verify(stored_hash, secret_source.password)
    except VerifyMismatchError as e:
        raise RuntimeError(
            "Startup password source does not match the stored UI password"
        ) from e


async def _seed_initial_password(
    db_client: IPCSocketClient,
    startup_password: str | None = None,
) -> None:
    """Seed or verify the initial UI password from configured startup sources."""
    check = await _db_send(db_client, "get_password_hash")
    if check.status != "success":
        logger.error(
            "Failed to check password status, skipping initial seed: %s",
            check.error_message,
        )
        return
    secret_source = _initial_secret_source(startup_password)
    if check.data and check.data.get("exists"):
        if secret_source is None:
            logger.debug("Password already exists in DB, skipping initial seed")
            return
        stored_hash = check.data.get("password_hash")
        if not isinstance(stored_hash, str):
            raise RuntimeError("Stored UI password hash is missing")
        _verify_existing_password_hash(
            stored_hash=stored_hash,
            secret_source=secret_source,
        )
        logger.debug("Startup password source matches stored UI password")
        return

    if secret_source is None:
        return
    if len(secret_source.password) < MIN_INITIAL_PASSWORD_LENGTH:
        logger.warning(
            "Initial UI password from %s is shorter than %d characters, skipping seed",
            secret_source.source,
            MIN_INITIAL_PASSWORD_LENGTH,
        )
        return

    password_hash = _ph.hash(secret_source.password)
    store = await _db_send(
        db_client,
        "store_password_hash",
        {"password_hash": password_hash},
    )
    if store.status == "success":
        logger.info("Initial UI password seeded from %s", secret_source.source)
        return
    logger.error("Failed to seed initial UI password: %s", store.error_message)


def _build_signing_handler(
    db_client: IPCSocketClient,
    private_key: Ed25519PrivateKey,
    rate_limiter: LoginRateLimiter,
) -> Callable[[str, DBOperation | object], Awaitable[DBResult]]:
    """Build the main signing proxy request handler."""

    async def signing_handler(
        type_name: str,
        message: DBOperation | object,
    ) -> DBResult:
        """Handle signing and auth operations."""
        del type_name
        validation_error = _validate_signing_operation(message)
        if validation_error is not None:
            return validation_error
        if not isinstance(message, DBOperation):
            return _error_result("unknown", "Expected DBOperation")

        token_result = _token_operation_result(message, private_key)
        if token_result is not None:
            return token_result
        if message.operation == "get_auth_status":
            return await _auth_status_result(message, db_client)

        setup_result = await _setup_password_result(
            message,
            db_client,
            private_key,
        )
        if setup_result is not None:
            return setup_result

        password_result = await _password_verification_result(
            message,
            db_client,
            private_key,
            rate_limiter,
        )
        if password_result is not None:
            return password_result

        return _error_result(message.request_id, "Unknown operation")

    return signing_handler


async def _start_signing_server(
    signing_socket_path: str,
    signing_secret: bytes,
    handler: Callable[[str, DBOperation | object], Awaitable[DBResult]],
    type_map: dict[str, type[DBOperation]],
) -> IPCSocketServer:
    """Start the signing proxy IPC server."""
    server = IPCSocketServer(
        socket_path=signing_socket_path,
        secret=signing_secret,
        handler=handler,
        type_map=type_map,
    )
    await server.start()
    logger.info(
        "Signing Proxy started (Socket: %s)",
        signing_socket_path,
    )
    return server


async def _wait_for_sigterm() -> None:
    """Block until the process receives SIGTERM."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    await stop_event.wait()


def _build_result(
    request_id: str,
    status: str,
    *,
    message: str | None = None,
    data: dict | None = None,
) -> DBResult:
    """Build one standard DBResult for signing operations."""
    return DBResult(
        request_id=request_id,
        status=status,
        error_message=message,
        data=data,
    )


def _error_result(
    request_id: str,
    message: str,
    *,
    data: dict | None = None,
) -> DBResult:
    """Build a standard error DBResult for signing operations."""
    return _build_result(request_id, "error", message=message, data=data)


def _success_result(request_id: str, data: dict | None = None) -> DBResult:
    """Build a standard success DBResult for signing operations."""
    return _build_result(request_id, "success", data=data)


def _token_success_result(request_id: str, key: str, value: object) -> DBResult:
    """Build one success result for a token-related payload field."""
    return _success_result(request_id, {key: value})


def _validate_signing_operation(
    message: DBOperation | object,
) -> DBResult | None:
    """Validate the incoming signing proxy operation envelope."""
    if not isinstance(message, DBOperation):
        return _error_result("unknown", "Expected DBOperation")
    if message.operation in SIGNING_OPERATIONS:
        return None
    return _error_result(
        message.request_id,
        f"Operation '{message.operation}' not allowed on signing proxy",
    )


def _token_operation_result(
    message: DBOperation,
    private_key: Ed25519PrivateKey,
) -> DBResult | None:
    """Handle JWT sign and verify operations."""
    params = message.parameters or {}
    if message.operation == "sign_token":
        claims = params.get("claims")
        if not isinstance(claims, dict):
            return _error_result(
                message.request_id,
                "sign_token requires 'claims' dict",
            )
        token = _encode_jwt(claims, private_key)
        return _token_success_result(message.request_id, "token", token)

    if message.operation == "verify_token":
        token = params.get("token")
        if not isinstance(token, str):
            return _error_result(
                message.request_id,
                "verify_token requires 'token' string",
            )
        decoded = _decode_jwt(token, private_key)
        if decoded is None:
            return _error_result(
                message.request_id,
                "Invalid or expired token",
            )
        return _token_success_result(message.request_id, "claims", decoded)
    return None


async def _auth_status_result(
    message: DBOperation,
    db_client: IPCSocketClient,
) -> DBResult:
    """Handle auth status lookup."""
    result = await _db_send(db_client, "get_password_hash")
    if result.status != "success":
        return _error_result(message.request_id, "Failed to check auth status")
    return _success_result(
        message.request_id,
        {"needs_setup": not result.data.get("exists", False)},
    )


async def _setup_password_result(
    message: DBOperation,
    db_client: IPCSocketClient,
    private_key: Ed25519PrivateKey,
) -> DBResult | None:
    """Handle first-time password setup."""
    if message.operation != "setup_password":
        return None
    password = (message.parameters or {}).get("password")
    if not isinstance(password, str) or len(password) < 8:
        return _error_result(
            message.request_id,
            "Password must be at least 8 characters",
        )

    check = await _db_send(db_client, "get_password_hash")
    if check.status == "success" and check.data.get("exists"):
        return _error_result(message.request_id, "password_already_exists")

    password_hash = _ph.hash(password)
    store = await _db_send(
        db_client,
        "store_password_hash",
        {"password_hash": password_hash},
    )
    if store.status != "success":
        return _error_result(message.request_id, "Failed to store password")
    return _success_result(message.request_id, _issue_tokens(private_key))


async def _password_verification_result(
    message: DBOperation,
    db_client: IPCSocketClient,
    private_key: Ed25519PrivateKey,
    rate_limiter: LoginRateLimiter,
) -> DBResult | None:
    """Handle verify_password and check_password operations."""
    if message.operation not in {"verify_password", "check_password"}:
        return None

    password = (message.parameters or {}).get("password")
    if not isinstance(password, str):
        return _error_result(
            message.request_id,
            f"{message.operation} requires 'password' string",
        )

    wait = rate_limiter.check()
    if wait > 0:
        return _error_result(
            message.request_id,
            "rate_limited",
            data={"retry_after": int(wait) + 1},
        )

    result = await _db_send(db_client, "get_password_hash")
    if result.status != "success" or not result.data.get("exists"):
        rate_limiter.record_failure()
        logger.warning("Login attempt with no password set")
        return _error_result(message.request_id, "invalid_password")

    stored_hash = result.data["password_hash"]
    try:
        _ph.verify(stored_hash, password)
    except VerifyMismatchError:
        rate_limiter.record_failure()
        logger.warning("Failed login attempt")
        return _error_result(message.request_id, "invalid_password")

    rate_limiter.reset()
    if message.operation == "check_password":
        return _success_result(message.request_id, {"ok": True})
    return _success_result(message.request_id, _issue_tokens(private_key))


def signing_proxy_main(
    signing_socket_path: str,
    signing_secret: bytes,
    db_socket_b_path: str,
    db_secret_b: bytes,
    startup_password: str | None = None,
) -> None:
    """Entry point for the Signing Proxy process.

    Args:
        signing_socket_path: Unix socket path facing FastAPI.
        signing_secret: HMAC secret for the Signing Socket.
        db_socket_b_path: Unix socket path to DB Proxy Socket B.
        db_secret_b: HMAC secret for Socket B.
        startup_password: Optional manual startup password kept in process memory.
    """
    type_map = {"DBOperation": DBOperation}
    db_type_map = {"DBResult": DBResult}

    async def run() -> None:
        """Fetch signing key, start server, run forever."""
        db_client = await _connect_signing_db_client(
            db_socket_b_path,
            db_secret_b,
            db_type_map,
        )
        private_key = await _load_signing_private_key(db_client)
        logger.info("Signing Proxy: key derived, DB connection kept for auth ops")
        await _seed_initial_password(db_client, startup_password)
        rate_limiter = LoginRateLimiter()
        signing_handler = _build_signing_handler(
            db_client,
            private_key,
            rate_limiter,
        )
        server = await _start_signing_server(
            signing_socket_path,
            signing_secret,
            signing_handler,
            type_map,
        )
        await _wait_for_sigterm()
        await server.stop()
        await db_client.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(run())
    loop.close()
