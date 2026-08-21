"""Behaviour contract tests for the chat route data models (F27).

The models are the carriers between chat helpers and route handlers;
the contract is that they are immutable and that mutable fields get an
independent default per instance.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.processes.fastapi_app.routes.chat_models import (
    ChatInputContext,
    PreparedChatRequest,
    PreparedFileCollection,
    PreparedFileInput,
    StoredChatFile,
)


class TestModelImmutability:
    """Every model is a frozen dataclass: fields cannot be reassigned."""

    @pytest.mark.parametrize(
        "model",
        [
            ChatInputContext(message="m", images=[], resolved_files=[]),
            PreparedChatRequest(conversation_id="c", ipc_request=None),
            StoredChatFile(
                file_id="f1",
                original_name="a.txt",
                mime_type="text/plain",
                size_bytes=3,
                file_path=Path("/tmp/a.txt"),
            ),
            PreparedFileInput(prompt_blocks=[], images=[], resolved_files=[]),
            PreparedFileCollection(prompt_blocks=[], images=[], resolved_files=[]),
        ],
        ids=[
            "ChatInputContext",
            "PreparedChatRequest",
            "StoredChatFile",
            "PreparedFileInput",
            "PreparedFileCollection",
        ],
    )
    def test_field_reassignment_is_rejected(self, model):
        """Mutating a field after construction raises FrozenInstanceError."""
        field = next(iter(type(model).__dataclass_fields__))
        with pytest.raises(FrozenInstanceError):
            setattr(model, field, "mutated")


class TestDefaults:
    """Mutable defaults are independent per instance."""

    def test_forced_tools_defaults_to_an_empty_list(self):
        """The default is an empty list, not a shared object."""
        first = ChatInputContext(message="m", images=[], resolved_files=[])
        second = ChatInputContext(message="m", images=[], resolved_files=[])
        assert first.forced_tools == []
        assert first.forced_tools is not second.forced_tools
