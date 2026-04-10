"""Base IPC client for DB Proxy channel communication.

Provides the shared send_operation infrastructure used by AdminDBClient,
SigningClient, and CredentialClient. Handles request building, timeout
management, error extraction, and HTTP exception translation for all
DB Proxy IPC channels.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from backend.ipc.ipc_channel import (
    IPCChannelError,
    IPCSocketClient,
)
from backend.ipc.models import (
    DBOperation,
    DBResult,
)


if TYPE_CHECKING:
    from pydantic import BaseModel


logger = logging.getLogger(__name__)


class _BaseIPCClient:
    """Base IPC client with send_operation for DB Proxy channels.

    Attributes:
        _ipc: Underlying IPC socket client.
        _timeout: Default operation timeout in seconds.
    """

    def __init__(
        self,
        ipc_client: IPCSocketClient,
        timeout: float = 30.0,
    ) -> None:
        self._ipc = ipc_client
        self._timeout = timeout

    async def send_operation(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        include_error_data: bool = False,
    ) -> dict[str, Any]:
        """Send a named operation via IPC.

        Args:
            operation: Operation name.
            parameters: Operation parameters.
            include_error_data: Include structured DB error payload in detail.

        Returns:
            Result data dict.

        Raises:
            HTTPException: On timeout, IPC error, or operation failure.
        """
        operation_request = self._build_operation_request(
            operation=operation,
            parameters=parameters,
        )
        try:
            response = await self._send_operation_request(operation_request)
        except TimeoutError as error:
            raise self._build_timeout_exception() from error
        except IPCChannelError as error:
            raise self._build_ipc_unavailable_exception() from error

        return self._extract_success_data(
            response=response,
            include_error_data=include_error_data,
        )

    async def _send_list_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Send one operation and return a list result field."""
        result = await self.send_operation(operation, parameters)
        list_result = result.get(result_key, [])
        if isinstance(list_result, list):
            return list_result
        return []

    async def _send_optional_dict_operation(
        self,
        operation: str,
        *,
        result_key: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send one operation and return an optional dict result field."""
        result = await self.send_operation(operation, parameters)
        dict_result = result.get(result_key)
        if isinstance(dict_result, dict):
            return dict_result
        return None

    def _build_operation_request(
        self,
        *,
        operation: str,
        parameters: dict[str, Any] | None,
    ) -> DBOperation:
        """Build one DB proxy operation request."""
        return DBOperation(
            request_id=str(uuid.uuid4()),
            operation=operation,
            parameters=parameters,
        )

    async def _send_operation_request(
        self,
        operation_request: DBOperation,
    ) -> DBResult | BaseModel:
        """Send one DB operation request through IPC."""
        return await asyncio.wait_for(
            self._ipc.send_and_receive(operation_request),
            timeout=self._timeout,
        )

    def _build_timeout_exception(self) -> HTTPException:
        """Build the standard timeout HTTP error for DB proxy calls."""
        return HTTPException(
            status_code=504,
            detail="Database operation timed out",
        )

    def _build_ipc_unavailable_exception(self) -> HTTPException:
        """Build the standard unavailable HTTP error for DB proxy calls."""
        return HTTPException(
            status_code=503,
            detail="Database proxy unavailable",
        )

    def _extract_success_data(
        self,
        *,
        response: DBResult | BaseModel,
        include_error_data: bool,
    ) -> dict[str, Any]:
        """Validate one DB proxy response and return its success payload."""
        if not isinstance(response, DBResult):
            raise HTTPException(
                status_code=502,
                detail="Unexpected response from DB Proxy",
            )
        if response.status == "error":
            raise HTTPException(
                status_code=500,
                detail=self._build_db_error_detail(
                    response=response,
                    include_error_data=include_error_data,
                ),
            )
        return response.data or {}

    def _build_db_error_detail(
        self,
        *,
        response: DBResult,
        include_error_data: bool,
    ) -> str | dict[str, Any]:
        """Build the DB proxy error detail payload for one failed response."""
        if include_error_data and response.data:
            return {
                "error": response.error_message or "Database error",
                **response.data,
            }
        return response.error_message or "Database error"
