"""Tests for the orchestrator process confirm handler builder."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.ipc.errors import (
    ConfirmationNotFoundError,
    OperationNotAllowedError,
    OperationNotFoundError,
)
from backend.ipc.models import ConfirmRequest
from backend.processes.orchestrator.client import DBProxyError
from backend.processes.orchestrator.process import _build_confirm_handler


def _request() -> ConfirmRequest:
    """Build one minimal confirm request."""
    return ConfirmRequest(
        request_id="r1",
        action_id="confirm-1",
        decision="approve",
        scope="once",
    )


def _handler_with(orch_error: Exception):
    """Build the confirm handler around one fixed orchestrator failure.

    Args:
        orch_error: Exception raised by the mocked resume call.

    Returns:
        The built async confirm handler.
    """
    orch = MagicMock()
    orch.resume_structured_confirmation = AsyncMock(side_effect=orch_error)
    return _build_confirm_handler(
        orch=orch,
        db_client=MagicMock(),
        get_llm_call=lambda: None,
        ipc_server_ref=lambda: MagicMock(),
    )


@pytest.mark.asyncio
async def test_unknown_confirmation_returns_typed_unknown_status() -> None:
    """A confirmation_unknown code resolves to the 'unknown' outcome.

    The handler classifies by the stable code, so the response no longer
    depends on the wording of the error message.
    """
    handler = _handler_with(
        DBProxyError(
            "DB Proxy operation 'decide_agent_confirmation' failed: "
            "confirmation 'x' not found",
            code=ConfirmationNotFoundError.code,
        )
    )
    response = await handler(_request())
    assert response.accepted is False
    assert response.status == "unknown"


@pytest.mark.asyncio
async def test_other_typed_code_propagates() -> None:
    """Any other typed DB proxy code is not silently swallowed."""
    handler = _handler_with(
        DBProxyError(
            "DB Proxy operation 'decide_agent_confirmation' failed: not allowed",
            code=OperationNotAllowedError.code,
        )
    )
    with pytest.raises(DBProxyError):
        await handler(_request())


@pytest.mark.asyncio
async def test_runtime_error_propagates() -> None:
    """A non-DB-proxy runtime failure is not treated as an unknown id."""
    handler = _handler_with(RuntimeError("state machine broken"))
    with pytest.raises(RuntimeError):
        await handler(_request())


def test_confirmation_not_found_is_a_value_error() -> None:
    """The typed error keeps its historical ValueError compatibility."""
    assert issubclass(ConfirmationNotFoundError, ValueError)
    error = ConfirmationNotFoundError("confirmation 'x' not found")
    assert error.code == "confirmation_unknown"
    assert str(error) == "confirmation 'x' not found"


def test_operation_not_found_keeps_plain_exception_base() -> None:
    """Operation errors stay plain exceptions; only the code is the contract."""
    error = OperationNotFoundError("operation 'x' not found")
    assert error.code == "operation_unknown"
    assert not isinstance(error, ValueError)
