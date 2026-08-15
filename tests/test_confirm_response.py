"""Tests for the HTTP response contract of RED tool confirmation decisions."""

from backend.processes.fastapi_app.routes.chat_helpers import build_confirm_response


def test_confirm_response_exposes_accepted_flag() -> None:
    """The web frontend reads ``accepted``; it must always be present."""
    approved = build_confirm_response(accepted=True, action_id="act-1")
    denied = build_confirm_response(accepted=False, action_id="act-1")
    assert approved["accepted"] is True
    assert denied["accepted"] is False


def test_confirm_response_preserves_detail_and_action_id() -> None:
    """Human-readable detail text and action id pass through unchanged."""
    approved = build_confirm_response(accepted=True, action_id="act-1")
    denied = build_confirm_response(accepted=False, action_id="act-1")
    assert approved["action_id"] == "act-1"
    assert approved["detail"] == "Approved"
    assert denied["action_id"] == "act-1"
    assert denied["detail"] == "Denied"


def test_confirm_response_includes_optional_fields_only_when_set() -> None:
    """Status, message id and summary appear only when provided."""
    full = build_confirm_response(
        accepted=True,
        action_id="act-1",
        status="final",
        message_id="msg-1",
        result_summary="done",
    )
    assert full["status"] == "final"
    assert full["message_id"] == "msg-1"
    assert full["result_summary"] == "done"
    minimal = build_confirm_response(accepted=True, action_id="act-1")
    assert "status" not in minimal
    assert "message_id" not in minimal
    assert "result_summary" not in minimal
