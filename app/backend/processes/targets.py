"""Process target functions and IPC socket configuration.

Each target function is the entry point for a child process spawned
by the supervisor.  They configure logging, then delegate to the
corresponding ``*_main()`` in their own module.

Socket path constants and health-check probes live here so that
both the supervisor (topology) and tests can import them without
pulling in the heavy orchestrator / DB proxy code.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# --- Socket paths (in /tmp for container runtime) ---
SOCKET_DIR = Path(tempfile.gettempdir()) / "bimemo-ipc"
DB_PROXY_SOCKET_A = str(SOCKET_DIR / "db-proxy-a.sock")
DB_PROXY_SOCKET_B = str(SOCKET_DIR / "db-proxy-b.sock")
DB_PROXY_SOCKET_C = str(SOCKET_DIR / "db-proxy-c.sock")
ORCHESTRATOR_SOCKET = str(SOCKET_DIR / "orchestrator.sock")
CREDENTIAL_PROXY_SOCKET = str(SOCKET_DIR / "credential-proxy.sock")
SIGNING_PROXY_SOCKET = str(SOCKET_DIR / "signing-proxy.sock")


def _ensure_socket_dir() -> None:
    """Create the IPC socket directory with safe permissions."""
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(str(SOCKET_DIR), 0o700)


def _default_diagnostics_database_path(database_path: str) -> str:
    """Derive the encrypted diagnostics DB path next to the main database."""
    database_file = Path(database_path)
    return str(database_file.with_name(f"{database_file.stem}_diagnostics.db"))


def _default_runtime_database_path(database_path: str) -> str:
    """Derive the encrypted runtime DB path next to the main database."""
    database_file = Path(database_path)
    return str(database_file.with_name(f"{database_file.stem}_runtime.db"))


def _ensure_parent_directory(file_path: str) -> None:
    """Create the parent directory for one configured file path."""
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)


def _db_proxy_servers(
    server_a: object,
    server_b: object,
    server_c: object,
) -> tuple[object, object, object]:
    """Return the three DB Proxy IPC servers in startup and shutdown order."""
    return server_a, server_b, server_c


def _build_db_proxy_handler_factory(
    *,
    allowlist: object,
    registry: object,
    db_operation_type: type,
    db_result_type: type,
) -> Callable[[str], Awaitable[Callable[[str, object], Awaitable[object]]]]:
    """Build the per-channel DB Proxy IPC handler factory."""

    async def make_handler(
        channel: str,
    ) -> Callable[[str, object], Awaitable[object]]:
        """Create an IPC message handler for one DB Proxy channel."""

        async def handler(
            type_name: str,
            message: object,
        ) -> object:
            """Dispatch DBOperation to the registry after allowlist checks."""
            del type_name
            if not isinstance(message, db_operation_type):
                return db_result_type(
                    request_id="unknown",
                    status="error",
                    error_message="Expected DBOperation",
                )
            if not allowlist.is_allowed(channel, message.operation):
                return db_result_type(
                    request_id=message.request_id,
                    status="error",
                    error_message=(
                        f"Operation '{message.operation}' not allowed on {channel}"
                    ),
                )
            return await registry.dispatch(message)

        return handler

    return make_handler


# ============================================================
# Process target functions
# ============================================================


async def _load_master_key(
    settings: object,
    startup_password: str | None = None,
) -> bytes:
    """Load the SQLCipher master key from the configured key source."""
    from backend.security.keysource import KeySourceError, get_key_source
    from backend.security.startup_password import load_startup_password

    key_source = get_key_source(
        settings.key_source,
        **_key_source_kwargs(
            settings=settings,
            startup_password=startup_password,
            password_loader=load_startup_password,
            key_source_error=KeySourceError,
        ),
    )
    return await key_source.get_key()


def _key_source_kwargs(
    *,
    settings: object,
    startup_password: str | None = None,
    password_loader: object,
    key_source_error: type[Exception],
) -> dict[str, str]:
    """Build key-source-specific keyword arguments from settings."""
    key_source = settings.key_source
    if key_source == "file":
        return {"file_path": settings.key_file_path}
    if key_source == "password":
        password = startup_password
        if password is None:
            if not getattr(settings, "automatic_unlock", False):
                raise key_source_error(
                    "Manual unlock password is required when automatic unlock "
                    "is disabled"
                )
            password_source = password_loader(
                password_file=settings.unlock_password_file,
                password_env=settings.unlock_password,
            )
            if password_source is None:
                raise key_source_error(
                    "Startup password is required when key_source is 'password'"
                )
            password = password_source.password
        return {
            "encrypted_key_file_path": settings.encrypted_key_file_path,
            "password": password,
        }
    raise key_source_error(f"Unsupported key_source: {key_source}")


def _open_primary_db(settings: object, master_key: bytes, logger: object) -> object:
    """Open and initialize the main encrypted application database."""
    from backend.memory.retrieval.fulltext import create_fts_tables
    from backend.memory.retrieval.vector_store import enable_vector_extension
    from backend.security.audit import initialize_audit_schema
    from backend.security.credential_schema import initialize_credential_schema
    from backend.security.database import DatabaseManager
    from backend.security.schema import initialize_schema

    _ensure_parent_directory(settings.database_path)
    db = DatabaseManager(settings.database_path)
    db.open(master_key)
    db.execute("SELECT 1")
    initialize_schema(db)
    initialize_audit_schema(db)
    initialize_credential_schema(db)
    create_fts_tables(db)
    try:
        enable_vector_extension(db)
    except Exception:
        logger.warning(
            "sqlite-vec initialization failed - vector search disabled",
            exc_info=True,
        )
    return db


def _open_diagnostics_db(settings: object, master_key: bytes) -> object:
    """Open and initialize the diagnostics database."""
    from backend.security.database import DatabaseManager
    from backend.security.diagnostics_schema import (
        initialize_diagnostics_schema,
        prune_memory_route_diagnostics,
    )

    diagnostics_database_path = (
        settings.diagnostics_database_path
        or _default_diagnostics_database_path(settings.database_path)
    )
    _ensure_parent_directory(diagnostics_database_path)
    diagnostics_db = DatabaseManager(diagnostics_database_path)
    diagnostics_db.open(master_key)
    initialize_diagnostics_schema(diagnostics_db)
    prune_memory_route_diagnostics(
        diagnostics_db,
        settings.diagnostics_retention_days,
    )
    return diagnostics_db


def _open_runtime_db(settings: object, master_key: bytes) -> object:
    """Open and initialize the short-lived assistant runtime database."""
    from backend.security.database import DatabaseManager
    from backend.security.runtime_schema import (
        initialize_runtime_schema,
        prune_runtime_state,
    )

    runtime_database_path = (
        settings.runtime_database_path
        or _default_runtime_database_path(settings.database_path)
    )
    _ensure_parent_directory(runtime_database_path)
    runtime_db = DatabaseManager(runtime_database_path)
    runtime_db.open(master_key)
    initialize_runtime_schema(runtime_db)
    prune_runtime_state(runtime_db)
    return runtime_db


async def _build_db_proxy_servers(
    *,
    handler_factory: Callable[
        [str],
        Awaitable[Callable[[str, object], Awaitable[object]]],
    ],
    type_map: dict[str, object],
    socket_a_path: str,
    secret_a: bytes,
    socket_b_path: str,
    secret_b: bytes,
    socket_c_path: str,
    secret_c: bytes,
) -> tuple[object, object, object]:
    """Build the three DB Proxy IPC servers for sockets A, B, and C."""
    from backend.ipc.ipc_channel import IPCSocketServer
    from backend.processes.db_proxy import CHANNEL_A, CHANNEL_B, CHANNEL_C

    handler_a = await handler_factory(CHANNEL_A)
    handler_b = await handler_factory(CHANNEL_B)
    handler_c = await handler_factory(CHANNEL_C)
    server_a = IPCSocketServer(
        socket_path=socket_a_path,
        secret=secret_a,
        handler=handler_a,
        type_map=type_map,
    )
    server_b = IPCSocketServer(
        socket_path=socket_b_path,
        secret=secret_b,
        handler=handler_b,
        type_map=type_map,
    )
    server_c = IPCSocketServer(
        socket_path=socket_c_path,
        secret=secret_c,
        handler=handler_c,
        type_map=type_map,
    )
    return server_a, server_b, server_c


async def _start_db_proxy_servers(
    server_a: object,
    server_b: object,
    server_c: object,
) -> None:
    """Start all three DB Proxy IPC servers."""
    for server in _db_proxy_servers(server_a, server_b, server_c):
        await server.start()


async def _stop_db_proxy_servers(
    server_a: object,
    server_b: object,
    server_c: object,
) -> None:
    """Stop all three DB Proxy IPC servers."""
    for server in _db_proxy_servers(server_a, server_b, server_c):
        await server.stop()


def _db_proxy_target(
    socket_a_path: str,
    secret_a: bytes,
    socket_b_path: str,
    secret_b: bytes,
    socket_c_path: str,
    secret_c: bytes,
    startup_password: str | None = None,
) -> None:
    """Entry point for the DB Proxy process.

    Opens SQLCipher, registers operations, starts tri-socket
    servers. Runs until terminated by the supervisor.

    Args:
        socket_a_path: Unix socket path for Orchestrator channel.
        secret_a: HMAC secret for Socket A.
        socket_b_path: Unix socket path for FastAPI admin channel.
        secret_b: HMAC secret for Socket B.
        socket_c_path: Unix socket path for Credential Proxy channel.
        secret_c: HMAC secret for Socket C.
        startup_password: Optional manual startup password kept in process memory.
    """
    import logging

    from backend.common import wait_for_sigterm
    from backend.config import get_settings
    from backend.ipc.models import DBOperation, DBResult
    from backend.logging_config import configure_logging
    from backend.processes.db_proxy import (
        build_allowlist,
        build_registry,
    )
    from backend.security.database import DatabaseManager
    from backend.security.keysource import get_key_source

    logger = logging.getLogger(__name__)
    settings = get_settings()
    configure_logging(settings.debug)
    _ = DatabaseManager, get_key_source

    type_map = {"DBOperation": DBOperation}
    loop = asyncio.new_event_loop()
    master_key = loop.run_until_complete(_load_master_key(settings, startup_password))
    core_db = _open_primary_db(settings, master_key, logger)
    runtime_db = _open_runtime_db(settings, master_key)
    diagnostics_db = _open_diagnostics_db(settings, master_key)
    registry = build_registry(
        core_db,
        settings.database_path,
        master_key,
        runtime_db=runtime_db,
        diagnostics_db=diagnostics_db,
        diagnostics_retention_days=settings.diagnostics_retention_days,
    )
    allowlist = build_allowlist()
    make_handler = _build_db_proxy_handler_factory(
        allowlist=allowlist,
        registry=registry,
        db_operation_type=DBOperation,
        db_result_type=DBResult,
    )

    async def run() -> None:
        """Start all three socket servers and run forever."""
        server_a, server_b, server_c = await _build_db_proxy_servers(
            handler_factory=make_handler,
            type_map=type_map,
            socket_a_path=socket_a_path,
            secret_a=secret_a,
            socket_b_path=socket_b_path,
            secret_b=secret_b,
            socket_c_path=socket_c_path,
            secret_c=secret_c,
        )
        await _start_db_proxy_servers(server_a, server_b, server_c)
        logger.info(
            "DB Proxy started (Socket A: %s, Socket B: %s, Socket C: %s)",
            socket_a_path,
            socket_b_path,
            socket_c_path,
        )

        from backend.processes.db_proxy.backup_loop import run_backup_loop

        backup_databases = {"core": core_db, "runtime": runtime_db}
        if diagnostics_db is not None:
            backup_databases["diagnostics"] = diagnostics_db
        backup_task = asyncio.create_task(
            run_backup_loop(
                backup_databases,
                settings.database_path,
                core_db,
                registry,
            )
        )

        await wait_for_sigterm()

        backup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await backup_task
        await _stop_db_proxy_servers(server_a, server_b, server_c)
        core_db.close()
        runtime_db.close()
        diagnostics_db.close()

    loop.run_until_complete(run())
    loop.close()


def _run_logged_process_target(
    target_main: Callable[..., None],
    **kwargs: object,
) -> None:
    """Load settings, configure logging, and run one process main function."""
    from backend.config import get_settings
    from backend.logging_config import configure_logging

    configure_logging(get_settings().debug)
    target_main(**kwargs)


def _orchestrator_target(
    db_proxy_socket_a: str,
    db_proxy_secret_a: bytes,
    frontend_socket: str,
    frontend_secret: bytes,
) -> None:
    """Entry point for the Orchestrator process.

    Connects to DB Proxy Socket A, spawns FS/Net workers,
    starts IPC server for FastAPI.

    Args:
        db_proxy_socket_a: Path to DB Proxy Socket A.
        db_proxy_secret_a: HMAC secret for Socket A.
        frontend_socket: Path for FastAPI-facing IPC socket.
        frontend_secret: HMAC secret for frontend channel.
    """
    from backend.processes.orchestrator import orchestrator_main

    _run_logged_process_target(
        orchestrator_main,
        db_proxy_socket_a=db_proxy_socket_a,
        db_proxy_secret_a=db_proxy_secret_a,
        frontend_socket=frontend_socket,
        frontend_secret=frontend_secret,
    )


def _credential_proxy_target(
    credential_socket_path: str,
    credential_secret: bytes,
    db_socket_c_path: str,
    db_secret_c: bytes,
) -> None:
    """Entry point for the Credential Proxy process.

    Args:
        credential_socket_path: Unix socket path facing FastAPI.
        credential_secret: HMAC secret for the Credential Socket.
        db_socket_c_path: Unix socket path to DB Proxy Socket C.
        db_secret_c: HMAC secret for Socket C.
    """
    from backend.processes.credential_proxy import credential_proxy_main

    _run_logged_process_target(
        credential_proxy_main,
        credential_socket_path=credential_socket_path,
        credential_secret=credential_secret,
        db_socket_c_path=db_socket_c_path,
        db_secret_c=db_secret_c,
    )


def _signing_proxy_target(
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
    from backend.processes.signing_proxy import signing_proxy_main

    _run_logged_process_target(
        signing_proxy_main,
        signing_socket_path=signing_socket_path,
        signing_secret=signing_secret,
        db_socket_b_path=db_socket_b_path,
        db_secret_b=db_secret_b,
        startup_password=startup_password,
    )


def _fastapi_target(
    orchestrator_socket: str,
    orchestrator_secret: bytes,
    db_proxy_socket_b: str,
    db_proxy_secret_b: bytes,
    credential_socket: str,
    credential_secret: bytes,
    signing_socket: str,
    signing_secret: bytes,
) -> None:
    """Entry point for the FastAPI process.

    Args:
        orchestrator_socket: Path to Orchestrator IPC socket.
        orchestrator_secret: HMAC secret for Orchestrator channel.
        db_proxy_socket_b: Path to DB Proxy Socket B.
        db_proxy_secret_b: HMAC secret for Socket B channel.
        credential_socket: Path to Credential Proxy IPC socket.
        credential_secret: HMAC secret for Credential channel.
        signing_socket: Path to Signing Proxy IPC socket.
        signing_secret: HMAC secret for Signing channel.
    """
    from backend.processes.fastapi_app import fastapi_main

    _run_logged_process_target(
        fastapi_main,
        orchestrator_socket=orchestrator_socket,
        orchestrator_secret=orchestrator_secret,
        db_proxy_socket_b=db_proxy_socket_b,
        db_proxy_secret_b=db_proxy_secret_b,
        credential_socket=credential_socket,
        credential_secret=credential_secret,
        signing_socket=signing_socket,
        signing_secret=signing_secret,
    )


# ============================================================
# Health check probes
# ============================================================


def _make_socket_probe(socket_path: str) -> Callable[[], Awaitable[bool]]:
    """Return an async callable that probes a Unix domain socket.

    The probe tries to open a connection to the given socket path.
    Returns True if the connection succeeds (server is listening),
    False if the socket doesn't exist or refuses connections.
    """

    async def _probe() -> bool:
        try:
            _reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.close()
            await writer.wait_closed()
            return True
        except FileNotFoundError, ConnectionRefusedError, OSError:
            return False

    return _probe
