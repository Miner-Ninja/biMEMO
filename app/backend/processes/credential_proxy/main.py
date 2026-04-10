"""Credential Proxy process main loop."""

import asyncio
import logging
import signal

from backend.ipc.ipc_channel import IPCSocketClient, IPCSocketServer
from backend.ipc.models import DBOperation, DBResult


logger = logging.getLogger(__name__)

# Operations this proxy is allowed to relay.
CREDENTIAL_OPERATIONS = frozenset(
    {
        "store_credential",
        "get_credential",
        "delete_credential",
        "create_integration",
        "list_integrations",
        "delete_integration",
        "store_llm_config",
        "get_llm_config_meta",
        "list_llm_configs",
        "create_llm_config",
        "update_llm_config",
        "delete_llm_config",
        "activate_llm_config",
        "get_llm_config_full",
    }
)


def _db_error_result(request_id: str, message: str) -> DBResult:
    """Build a standard DB proxy error result."""
    return DBResult(
        request_id=request_id,
        status="error",
        error_message=message,
    )


def _is_allowed_credential_operation(operation: str) -> bool:
    """Return whether one DB operation may pass through the credential proxy."""
    return operation in CREDENTIAL_OPERATIONS


async def _wait_for_sigterm() -> None:
    """Wait until the process receives SIGTERM."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    await stop_event.wait()


def credential_proxy_main(
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
    type_map = {"DBOperation": DBOperation}
    db_type_map = {"DBResult": DBResult}

    async def _relay_operation(
        db_client: IPCSocketClient,
        message: DBOperation | object,
    ) -> DBResult:
        """Validate and relay one credential operation to the DB proxy."""
        if not isinstance(message, DBOperation):
            return _db_error_result("unknown", "Expected DBOperation")

        if not _is_allowed_credential_operation(message.operation):
            return _db_error_result(
                message.request_id,
                f"Operation '{message.operation}' not allowed on credential proxy",
            )

        response = await db_client.send_and_receive(message)
        if isinstance(response, DBResult):
            return response
        return _db_error_result(
            message.request_id,
            "Unexpected response from DB Proxy",
        )

    async def run() -> None:
        """Connect to DB Proxy Socket C, start server, run forever."""
        db_client = IPCSocketClient(
            socket_path=db_socket_c_path,
            secret=db_secret_c,
            type_map=db_type_map,
        )
        await db_client.connect()

        server = IPCSocketServer(
            socket_path=credential_socket_path,
            secret=credential_secret,
            handler=lambda _type_name, message: _relay_operation(db_client, message),
            type_map=type_map,
        )
        await server.start()
        logger.info(
            "Credential Proxy started (Socket: %s, DB: %s)",
            credential_socket_path,
            db_socket_c_path,
        )
        await _wait_for_sigterm()

        await server.stop()
        await db_client.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(run())
    loop.close()
