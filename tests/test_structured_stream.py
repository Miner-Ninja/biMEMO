"""Tests for the incremental JSON-schema answer-field extractor."""

from __future__ import annotations

from backend.llm.structured_stream import AnswerFieldStreamExtractor


def _feed_in_chunks(chunks: list[str]) -> tuple[str, AnswerFieldStreamExtractor]:
    """Feed chunks sequentially and return the concatenated decoded delta."""
    extractor = AnswerFieldStreamExtractor()
    output: list[str] = []
    for chunk in chunks:
        output.append(extractor.feed(chunk))
    return "".join(output), extractor


class TestHappyPaths:
    """Tests covering the common streaming scenarios."""

    def test_single_chunk_with_complete_answer(self) -> None:
        """Return the full answer text when the buffer arrives whole."""
        decoded, extractor = _feed_in_chunks(['{"answer":"hello"}'])
        assert decoded == "hello"
        assert extractor.is_done

    def test_answer_split_across_many_chunks(self) -> None:
        """Concatenate deltas from a multi-chunk answer string."""
        decoded, extractor = _feed_in_chunks(
            ['{"answer":"hel', "lo wo", 'rld"}'],
        )
        assert decoded == "hello world"
        assert extractor.is_done

    def test_preamble_chunk_emits_nothing(self) -> None:
        """Emit nothing while the ``"answer"`` key has not arrived yet."""
        extractor = AnswerFieldStreamExtractor()
        assert extractor.feed('{"ans') == ""
        assert extractor.feed('wer":"hi"}') == "hi"
        assert extractor.is_done

    def test_trailing_fields_never_emit(self) -> None:
        """Discard sql and suggestions silently after the answer closes."""
        decoded, extractor = _feed_in_chunks(
            ['{"answer":"done","sql":"SELECT 1","suggestions":["next"]}'],
        )
        assert decoded == "done"
        assert extractor.is_done
        assert extractor.full_text.endswith('"suggestions":["next"]}')

    def test_full_text_preserves_raw_buffer(self) -> None:
        """Preserve the exact raw JSON text for post-stream parsing."""
        chunks = ['{"answer":"a', '","sql":null,"suggestions":[]}']
        _, extractor = _feed_in_chunks(chunks)
        assert extractor.full_text == "".join(chunks)

    def test_whitespace_around_key_allowed(self) -> None:
        """Accept optional whitespace around the colon and opening quote."""
        decoded, extractor = _feed_in_chunks(
            ['{  "answer"   :   ', '"padded"}'],
        )
        assert decoded == "padded"
        assert extractor.is_done


class TestEscapeSequences:
    """Tests covering JSON string escape sequences and partial escapes."""

    def test_simple_escapes_decoded(self) -> None:
        r"""Translate the standard JSON single-character escapes."""
        decoded, _ = _feed_in_chunks(
            [r'{"answer":"line1\nline2\ttab\"quote\\slash"}'],
        )
        assert decoded == 'line1\nline2\ttab"quote\\slash'

    def test_escaped_quote_split_across_chunks(self) -> None:
        """Buffer an escape that straddles a chunk boundary."""
        extractor = AnswerFieldStreamExtractor()
        first = extractor.feed('{"answer":"a\\')
        second = extractor.feed('"b"}')
        assert first == "a"
        assert second == '"b'
        assert extractor.is_done

    def test_backslash_split_before_body_arrives(self) -> None:
        """Stall until the escape body shows up in the next chunk."""
        extractor = AnswerFieldStreamExtractor()
        assert extractor.feed('{"answer":"x\\') == "x"
        assert extractor.feed('nY"}') == "\nY"
        assert extractor.is_done

    def test_unicode_escape_split_across_chunks(self) -> None:
        r"""Buffer ``\uXXXX`` escapes until all four hex digits arrive."""
        extractor = AnswerFieldStreamExtractor()
        first = extractor.feed('{"answer":"\\u00')
        second = extractor.feed('e9"}')
        assert first == ""
        assert second == "é"
        assert extractor.is_done

    def test_unicode_escape_in_one_chunk(self) -> None:
        r"""Decode ``\uXXXX`` when the whole sequence arrives at once."""
        decoded, _ = _feed_in_chunks([r'{"answer":"\u00e9clair"}'])
        assert decoded == "éclair"


class TestEdgeCases:
    """Tests covering malformed streams and exotic scenarios."""

    def test_unterminated_value_stays_in_value_state(self) -> None:
        """Leave the extractor mid-value when no closing quote arrives."""
        extractor = AnswerFieldStreamExtractor()
        emitted = extractor.feed('{"answer":"hello')
        assert emitted == "hello"
        assert not extractor.is_done
        assert extractor.full_text == '{"answer":"hello'

    def test_only_key_prefix_returns_empty(self) -> None:
        """Return empty string when the answer key has not appeared yet."""
        extractor = AnswerFieldStreamExtractor()
        assert extractor.feed("") == ""
        assert extractor.feed('{"ot') == ""
        assert extractor.feed('her":1,"ans') == ""
        assert not extractor.is_done

    def test_empty_answer_value(self) -> None:
        """Return an empty string when the answer field is an empty string."""
        decoded, extractor = _feed_in_chunks(['{"answer":"","sql":null}'])
        assert decoded == ""
        assert extractor.is_done

    def test_chunks_after_close_are_discarded(self) -> None:
        """Ignore further chunks once the answer has closed."""
        extractor = AnswerFieldStreamExtractor()
        assert extractor.feed('{"answer":"done"}') == "done"
        assert extractor.feed(',"sql":"SELECT 1"}') == ""
        assert extractor.is_done


class TestDecodeEscapeHelperGuards:
    """Ensure decoding does not raise on pathological partial buffers."""

    def test_feed_does_not_raise_on_lone_backslash(self) -> None:
        """Feed a buffer that ends with a bare backslash without crashing."""
        extractor = AnswerFieldStreamExtractor()
        assert extractor.feed('{"answer":"x\\') == "x"
        # The lone backslash remains pending, so the next feed decodes it.
        assert extractor.feed('n"}') == "\n"

    def test_invalid_unicode_hex_falls_back_to_raw(self) -> None:
        r"""Decode ``\uZZZZ`` by emitting the raw four-char body literally."""
        decoded, _ = _feed_in_chunks([r'{"answer":"bad\uZZZZ"}'])
        assert decoded == "badZZZZ"

    def test_unknown_simple_escape_falls_back_to_body(self) -> None:
        r"""Decode ``\q`` as a plain ``q`` character (permissive)."""
        decoded, _ = _feed_in_chunks([r'{"answer":"foo\qbar"}'])
        assert decoded == "fooqbar"
