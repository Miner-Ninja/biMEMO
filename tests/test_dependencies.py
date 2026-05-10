"""Tests for active FastAPI process dependency helpers.

Exercises the dependency functions used by the active multi-process
FastAPI application.
"""

from unittest.mock import MagicMock

import pytest
from backend.processes.fastapi_app.deps import (
    _get_admin_client,
    _get_audit_fn,
    _get_credential_client,
    _get_csrf_token_for_jti,
    _get_orchestrator_client,
    _get_realtime_manager,
    _get_reauth_manager,
    _get_session_manager,
    _get_signing_client,
    _require_auth,
)
from fastapi import HTTPException


def _make_request(state_attrs: dict) -> MagicMock:
    """Create a mock request with app.state attributes."""
    request = MagicMock()
    app_state = MagicMock()
    for key, value in state_attrs.items():
        setattr(app_state, key, value)
    request.app.state = app_state
    return request


class TestDependencyFunctions:
    """Tests for active-process dependency retrieval."""

    def test_get_orchestrator_client(self):
        """Verify orchestrator client is returned when connected."""
        mock = MagicMock()
        mock.is_connected = True
        req = _make_request({"orchestrator_client": mock})
        assert _get_orchestrator_client(req) is mock

    def test_get_orchestrator_client_raises_when_disconnected(self):
        """Verify disconnected orchestrator raises 503."""
        mock = MagicMock()
        mock.is_connected = False
        req = _make_request({"orchestrator_client": mock})
        with pytest.raises(HTTPException) as exc_info:
            _get_orchestrator_client(req)
        assert exc_info.value.status_code == 503

    def test_get_session_manager(self):
        """Verify session manager retrieved from state."""
        mock = MagicMock()
        req = _make_request({"session_manager": mock})
        assert _get_session_manager(req) is mock

    def test_get_realtime_manager(self):
        """Verify realtime manager retrieved from state."""
        mock = MagicMock()
        req = _make_request({"realtime_manager": mock})
        assert _get_realtime_manager(req) is mock

    def test_get_reauth_manager(self):
        """Verify reauth manager retrieved from state."""
        mock = MagicMock()
        req = _make_request({"reauth_manager": mock})
        assert _get_reauth_manager(req) is mock

    def test_get_admin_client(self):
        """Verify admin client retrieved from state."""
        mock = MagicMock()
        req = _make_request({"admin_client": mock})
        assert _get_admin_client(req) is mock

    def test_get_credential_client(self):
        """Verify credential client retrieved from state."""
        mock = MagicMock()
        req = _make_request({"credential_client": mock})
        assert _get_credential_client(req) is mock

    def test_get_credential_client_raises_when_missing(self):
        """Verify missing credential client raises 503."""
        req = _make_request({"credential_client": None})
        with pytest.raises(HTTPException) as exc_info:
            _get_credential_client(req)
        assert exc_info.value.status_code == 503

    def test_get_signing_client(self):
        """Verify signing client retrieved from state."""
        mock = MagicMock()
        req = _make_request({"signing_client": mock})
        assert _get_signing_client(req) is mock

    def test_get_signing_client_raises_when_missing(self):
        """Verify missing signing client raises 503."""
        req = _make_request({"signing_client": None})
        with pytest.raises(HTTPException) as exc_info:
            _get_signing_client(req)
        assert exc_info.value.status_code == 503

    def test_get_audit_fn(self):
        """Verify audit function retrieved from state."""
        mock = MagicMock()
        req = _make_request({"audit_fn": mock})
        assert _get_audit_fn(req) is mock

    def test_get_csrf_token_for_jti(self):
        """Verify CSRF token lookup returns stored token."""
        from backend.processes.fastapi_app import deps as deps_module

        deps_module._csrf_tokens.clear()
        deps_module._csrf_tokens["jti-1"] = "csrf-token"
        assert _get_csrf_token_for_jti("jti-1") == "csrf-token"
        assert _get_csrf_token_for_jti("missing") is None


class TestRequireAuth:
    """Tests for authentication requirement."""

    def test_claims_present(self):
        """Verify claims returned when present."""
        claims = MagicMock()
        request = MagicMock()
        request.state._state = {"claims": claims}
        result = _require_auth(request)
        assert result is claims

    def test_claims_missing_raises_401(self):
        """Verify 401 raised when claims missing."""
        request = MagicMock()
        request.state._state = {}
        with pytest.raises(HTTPException) as exc_info:
            _require_auth(request)
        assert exc_info.value.status_code == 401
