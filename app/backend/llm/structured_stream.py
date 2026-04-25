"""Incremental extractor for the ``answer`` field of a streaming JSON schema.

When an LLM streams ``{"answer": "...", "sql": "...", ...}`` token by token,
the browser only wants the visible answer text — not the raw JSON syntax and
not the other schema fields. :class:`AnswerFieldStreamExtractor` eats each
raw chunk, finds the start of the ``"answer"`` string value, and returns the
newly-decoded characters belonging to that field. Escape sequences and
Unicode escapes that straddle chunk boundaries are buffered until the full
sequence has arrived, so the caller never sees a half-decoded escape.
"""

from __future__ import annotations

from enum import Enum


# Simple escape -> literal mapping for single-character JSON escapes.
_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class _ExtractorState(Enum):
    """Tracks where in the JSON structure the extractor currently is."""

    SEARCHING = "searching"
    IN_VALUE = "in_value"
    DONE = "done"


class AnswerFieldStreamExtractor:
    r"""Stream-friendly extractor for the top-level ``"answer"`` string.

    Call :meth:`feed` with each raw JSON chunk as it arrives from the LLM;
    it returns the newly-decoded characters of the answer value that were
    not returned on earlier calls. Chunks before the ``"answer"`` key and
    chunks after the closing quote of the answer value are silently
    absorbed. Invalid escape sequences still decode to their body
    character (permissive, matching ``json.loads`` for ``\/`` and falling
    back to the raw payload for unknown escapes).

    The raw accumulated JSON text is preserved on :attr:`full_text` so the
    caller can pass it to ``parse_structured_response_json`` once the
    stream has closed.
    """

    def __init__(self) -> None:
        """Initialize the extractor in the :attr:`SEARCHING` state."""
        self._buffer = ""
        self._state = _ExtractorState.SEARCHING
        self._value_cursor = 0
        self._pending_escape: str | None = None

    @property
    def full_text(self) -> str:
        """Return the accumulated raw JSON text seen so far."""
        return self._buffer

    @property
    def is_done(self) -> bool:
        """Whether the closing quote of the answer value has been consumed."""
        return self._state is _ExtractorState.DONE

    def feed(self, delta: str) -> str:
        """Append one chunk of raw JSON text and return the decoded delta.

        Args:
            delta: New raw JSON characters as received from the provider.

        Returns:
            The newly-decoded characters that belong to the ``answer``
            value. Empty when the answer has not started yet, has already
            closed, or only a partial escape has arrived.
        """
        if not delta:
            return ""
        self._buffer += delta
        if self._state is _ExtractorState.DONE:
            return ""
        if (
            self._state is _ExtractorState.SEARCHING
            and not self._advance_to_answer_value()
        ):
            return ""
        return self._consume_value_segment()

    def _advance_to_answer_value(self) -> bool:
        """Move the cursor past ``"answer":"`` when it is fully present."""
        key = '"answer"'
        key_index = self._buffer.find(key)
        if key_index < 0:
            return False
        cursor = key_index + len(key)
        cursor = _skip_whitespace(self._buffer, cursor)
        if cursor >= len(self._buffer) or self._buffer[cursor] != ":":
            return False
        cursor += 1
        cursor = _skip_whitespace(self._buffer, cursor)
        if cursor >= len(self._buffer) or self._buffer[cursor] != '"':
            return False
        cursor += 1
        self._value_cursor = cursor
        self._state = _ExtractorState.IN_VALUE
        return True

    def _consume_value_segment(self) -> str:
        """Decode as many answer characters as the buffer currently allows."""
        decoded: list[str] = []
        cursor = self._value_cursor
        buffer_length = len(self._buffer)
        if self._pending_escape is not None:
            needed = 5 if self._pending_escape.startswith("u") else 1
            if not self._pending_escape:
                looks_like_unicode = (
                    cursor < buffer_length and self._buffer[cursor] == "u"
                )
                needed = 5 if looks_like_unicode else 1
            while len(self._pending_escape) < needed and cursor < buffer_length:
                self._pending_escape += self._buffer[cursor]
                cursor += 1
                if self._pending_escape == "u":
                    needed = 5
            if len(self._pending_escape) < needed:
                self._value_cursor = cursor
                return ""
            consumed, emitted = _decode_escape_sequence(self._pending_escape)
            if consumed == 0:
                self._value_cursor = cursor
                return ""
            decoded.append(emitted)
            self._pending_escape = None
        while cursor < buffer_length:
            char = self._buffer[cursor]
            if char == "\\":
                remaining = self._buffer[cursor + 1 :]
                consumed, emitted = _decode_escape_sequence(remaining)
                if consumed == 0:
                    self._pending_escape = remaining
                    cursor = buffer_length
                    break
                decoded.append(emitted)
                cursor += 1 + consumed
                continue
            if char == '"':
                cursor += 1
                self._value_cursor = cursor
                self._state = _ExtractorState.DONE
                return "".join(decoded)
            decoded.append(char)
            cursor += 1
        self._value_cursor = cursor
        return "".join(decoded)


def _skip_whitespace(text: str, index: int) -> int:
    """Advance past ASCII whitespace starting at ``index``."""
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _decode_escape_sequence(body: str) -> tuple[int, str]:
    r"""Decode one JSON escape body (the text after the leading ``\``).

    Args:
        body: The characters immediately after the backslash.

    Returns:
        Tuple ``(consumed, emitted)`` where ``consumed`` is the number
        of characters from ``body`` that were used and ``emitted`` is
        the decoded literal. Returns ``(0, "")`` when the body is too
        short to represent a complete escape yet.
    """
    if not body:
        return 0, ""
    head = body[0]
    if head in _SIMPLE_ESCAPES:
        return 1, _SIMPLE_ESCAPES[head]
    if head != "u":
        return 1, head
    if len(body) < 5:
        return 0, ""
    hex_digits = body[1:5]
    try:
        code_point = int(hex_digits, 16)
    except ValueError:
        return 5, hex_digits
    return 5, chr(code_point)
