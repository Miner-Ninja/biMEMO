"""Tests for Telegram channel adapter.

Covers allowlist filtering, message chunking,
parameter masking, confirmations, and privacy.
"""

from backend.channels.telegram import (
    CREDENTIAL_LEAK_WARNING,
    PRIVACY_WARNING,
    STOP_CALLBACK_DATA,
    ConfirmationMessage,
    PrivacyState,
    TelegramAdapter,
    TelegramConfig,
    ThinkingIndicator,
    chunk_message,
    format_confirmation,
    is_allowed_user,
    make_stop_keyboard,
    mask_body,
    mask_email,
    mask_path,
    mask_url,
)


class TestAllowlist:
    """Tests for user ID allowlist filtering."""

    def test_allowed_user(self):
        """Verify allowed user passes."""
        config = TelegramConfig(
            bot_token="token",
            allowed_user_ids=frozenset({123, 456}),
        )
        assert is_allowed_user(123, config)

    def test_denied_user(self):
        """Verify denied user blocked."""
        config = TelegramConfig(
            bot_token="token",
            allowed_user_ids=frozenset({123}),
        )
        assert not is_allowed_user(999, config)


class TestChunking:
    """Tests for message chunking."""

    def test_short_message_no_chunk(self):
        """Verify short message returns single chunk."""
        chunks = chunk_message("hello")
        assert len(chunks) == 1
        assert chunks[0] == "hello"

    def test_long_message_chunks(self):
        """Verify long message is chunked."""
        text = "a" * 5000
        chunks = chunk_message(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 4096

    def test_splits_at_paragraph(self):
        """Verify splits at paragraph boundary."""
        text = "a" * 4000 + "\n\n" + "b" * 100
        chunks = chunk_message(text)
        assert len(chunks) == 2

    def test_splits_at_newline(self):
        """Verify splits at newline when no paragraph."""
        text = "a" * 4000 + "\n" + "b" * 100
        chunks = chunk_message(text)
        assert len(chunks) == 2

    def test_trailing_newlines_only(self):
        """Verify handles trailing newlines after split."""
        text = "a" * 4096 + "\n\n"
        chunks = chunk_message(text)
        assert len(chunks) == 1
        assert chunks[0] == "a" * 4096

    def test_hard_split_no_newlines(self):
        """Verify hard split at limit with no newlines."""
        text = "a" * 5000
        chunks = chunk_message(text)
        assert chunks[0] == "a" * 4096
        assert chunks[1] == "a" * 904


class TestMaskEmail:
    """Tests for email masking."""

    def test_masks_email(self):
        """Verify email is masked."""
        result = mask_email("ivan@example.com")
        assert "iv**" in result
        assert "ex****e" in result

    def test_short_local(self):
        """Verify short local part preserved."""
        result = mask_email("ab@x.com")
        assert "@" in result

    def test_invalid_email(self):
        """Verify invalid email returned as-is."""
        assert mask_email("notanemail") == "notanemail"


class TestMaskUrl:
    """Tests for URL masking."""

    def test_masks_url(self):
        """Verify URL domain is masked."""
        result = mask_url("https://example.com/path")
        assert "ex****e" in result
        assert "/***" in result

    def test_invalid_url(self):
        """Verify invalid URL returned as-is."""
        assert mask_url("notaurl") == "notaurl"


class TestMaskPath:
    """Tests for path masking."""

    def test_masks_path(self):
        """Verify path segments are masked."""
        result = mask_path("/home/user/secret.txt")
        assert "us***r" in result or "se***t" in result

    def test_short_segments(self):
        """Verify short segments preserved."""
        result = mask_path("/a/b/c")
        assert result == "/a/b/c"


class TestMaskBody:
    """Tests for body masking."""

    def test_short_body_unmasked(self):
        """Verify short body is not masked."""
        assert mask_body("hi") == "hi"

    def test_long_body_masked(self):
        """Verify long body is masked."""
        result = mask_body("This is a long body text")
        assert result.startswith("This is a ")
        assert "[...masked...]" in result


class TestFormatConfirmation:
    """Tests for trust confirmation formatting."""

    def test_formats_confirmation(self):
        """Verify confirmation message format."""
        msg = format_confirmation(
            action_id="abc123",
            tool_name="http_fetch",
            parameters={"url": "https://example.com/p"},
        )
        assert isinstance(msg, ConfirmationMessage)
        assert msg.action_id == "abc123"
        assert msg.tool_name == "http_fetch"
        assert "http_fetch" in msg.text
        assert "Approve" in msg.text

    def test_masks_email_parameter(self):
        """Verify email parameter is masked."""
        msg = format_confirmation(
            action_id="x",
            tool_name="email_send",
            parameters={
                "to_email": "user@example.com",
            },
        )
        assert "us**" in msg.text
        assert "user@example.com" not in msg.text

    def test_masks_url_parameter(self):
        """Verify URL parameter is masked."""
        msg = format_confirmation(
            action_id="x",
            tool_name="http_fetch",
            parameters={
                "url": "https://example.com/secret",
            },
        )
        assert "/secret" not in msg.text

    def test_masks_path_parameter(self):
        """Verify path parameter is masked."""
        msg = format_confirmation(
            action_id="x",
            tool_name="file_write",
            parameters={
                "path": "/home/user/data.txt",
            },
        )
        assert "us***r" in msg.text

    def test_masks_body_parameter(self):
        """Verify body parameter is masked."""
        msg = format_confirmation(
            action_id="x",
            tool_name="email_send",
            parameters={
                "body": "Very secret long content here",
            },
        )
        assert "[...masked...]" in msg.text

    def test_masks_generic_parameter(self):
        """Verify unknown parameter is body-masked."""
        msg = format_confirmation(
            action_id="x",
            tool_name="test",
            parameters={
                "data": "Some long data value here",
            },
        )
        assert "[...masked...]" in msg.text


class TestThinkingIndicator:
    """Tests for thinking indicator state."""

    def test_start_sets_thinking(self):
        """Verify start sets thinking state."""
        indicator = ThinkingIndicator()
        indicator.start(42, 100)
        assert indicator.is_thinking
        assert indicator.message_id == 42

    def test_start_stores_chat_id(self):
        """Verify start stores chat_id."""
        indicator = ThinkingIndicator()
        indicator.start(42, 100)
        assert indicator.chat_id == 100

    def test_stop_returns_message_and_chat_id(self):
        """Verify stop returns message_id and chat_id."""
        indicator = ThinkingIndicator()
        indicator.start(42, 100)
        msg_id, chat_id = indicator.stop()
        assert msg_id == 42
        assert chat_id == 100
        assert not indicator.is_thinking
        assert indicator.message_id is None

    def test_stop_clears_chat_id(self):
        """Verify stop clears chat_id."""
        indicator = ThinkingIndicator()
        indicator.start(42, 100)
        indicator.stop()
        assert indicator.chat_id is None

    def test_stop_without_start(self):
        """Verify stop without start returns None tuple."""
        indicator = ThinkingIndicator()
        msg_id, chat_id = indicator.stop()
        assert msg_id is None
        assert chat_id is None

    def test_handle_stop_callback_active_matching(self):
        """Verify returns True when active and matching."""
        indicator = ThinkingIndicator()
        indicator.start(42, 100)
        assert indicator.handle_stop_callback(STOP_CALLBACK_DATA)

    def test_handle_stop_callback_not_thinking(self):
        """Verify returns False when not thinking."""
        indicator = ThinkingIndicator()
        assert not indicator.handle_stop_callback(STOP_CALLBACK_DATA)

    def test_handle_stop_callback_wrong_data(self):
        """Verify returns False with wrong callback data."""
        indicator = ThinkingIndicator()
        indicator.start(42, 100)
        assert not indicator.handle_stop_callback("wrong_data")


class TestMakeStopKeyboard:
    """Tests for stop keyboard data structure."""

    def test_returns_correct_structure(self):
        """Verify keyboard has one row, one button."""
        keyboard = make_stop_keyboard()
        assert len(keyboard) == 1
        assert len(keyboard[0]) == 1
        button = keyboard[0][0]
        assert button["text"] == "Stop"
        assert button["callback_data"] == STOP_CALLBACK_DATA


class TestTelegramAdapter:
    """Tests for Telegram adapter routing."""

    def _make_adapter(self) -> TelegramAdapter:
        """Create adapter with test config."""
        config = TelegramConfig(
            bot_token="token",
            allowed_user_ids=frozenset({123}),
        )
        return TelegramAdapter(config=config)

    def test_denied_user_returns_none(self):
        """Verify denied user gets no response."""
        adapter = self._make_adapter()
        adapter.acknowledge_privacy(123)
        assert adapter.handle_message(999, "hi") is None

    def test_unacknowledged_returns_warning(self):
        """Verify unacknowledged user gets warning."""
        adapter = self._make_adapter()
        result = adapter.handle_message(123, "hi")
        assert result == PRIVACY_WARNING

    def test_acknowledged_returns_message(self):
        """Verify acknowledged user gets echoed."""
        adapter = self._make_adapter()
        adapter.acknowledge_privacy(123)
        assert adapter.privacy_states[123] == PrivacyState.ACKNOWLEDGED
        result = adapter.handle_message(123, "hello")
        assert result == "hello"

    def test_per_user_privacy_isolation(self):
        """Verify user A ack does not affect user B."""
        config = TelegramConfig(
            bot_token="token",
            allowed_user_ids=frozenset({123, 456}),
        )
        adapter = TelegramAdapter(config=config)
        adapter.acknowledge_privacy(123)
        assert adapter.handle_message(123, "hi") == "hi"
        assert adapter.handle_message(456, "hi") == PRIVACY_WARNING

    def test_message_queued_on_privacy_warning(self):
        """Verify message stored when privacy not acknowledged."""
        adapter = self._make_adapter()
        adapter.handle_message(123, "my message")
        assert adapter.pop_pending_message(123) == "my message"

    def test_pop_pending_message_clears(self):
        """Verify pop clears the queued message."""
        adapter = self._make_adapter()
        adapter.handle_message(123, "msg")
        adapter.pop_pending_message(123)
        assert adapter.pop_pending_message(123) is None

    def test_pop_pending_message_no_queue(self):
        """Verify pop returns None when nothing queued."""
        adapter = self._make_adapter()
        assert adapter.pop_pending_message(123) is None

    def test_thinking_accessor(self):
        """Verify thinking indicator accessible."""
        adapter = self._make_adapter()
        adapter.thinking.start(10, 200)
        assert adapter.thinking.is_thinking


class TestPrivacyWarning:
    """Tests for privacy warning constants."""

    def test_warning_mentions_telegram(self):
        """Verify warning mentions Telegram servers."""
        assert "Telegram" in PRIVACY_WARNING

    def test_credential_warning_exists(self):
        """Verify credential leak warning text."""
        assert "Credential" in CREDENTIAL_LEAK_WARNING
