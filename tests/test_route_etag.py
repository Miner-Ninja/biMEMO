"""Behaviour contract tests for the ETag / If-None-Match helpers (F27).

Pins the conditional-request matching rules the REST contract relies on:
absent header is a non-match, ``*`` matches anything, and a
comma-separated list matches when the candidate ETag is among the
entries (surrounding whitespace ignored).
"""

from __future__ import annotations

from backend.processes.fastapi_app.routes.etag import if_none_match_matches


ETAG = '"abc123"'


class TestIfNoneMatchMatches:
    """S2: the matching rule for one If-None-Match value and one ETag."""

    def test_absent_header_is_a_non_match(self):
        """No header means the client sent no condition."""
        assert if_none_match_matches(None, ETAG) is False

    def test_empty_header_is_a_non_match(self):
        """An empty header is treated as absent."""
        assert if_none_match_matches("", ETAG) is False
        assert if_none_match_matches("   ", ETAG) is False

    def test_wildcard_matches(self):
        """A bare ``*`` (with optional surrounding spaces) matches."""
        assert if_none_match_matches("*", ETAG) is True
        assert if_none_match_matches(" * ", ETAG) is True

    def test_single_exact_match(self):
        """The header carrying the candidate ETag matches."""
        assert if_none_match_matches(ETAG, ETAG) is True

    def test_single_mismatch(self):
        """A different ETag does not match."""
        assert if_none_match_matches('"other"', ETAG) is False

    def test_comma_separated_list_containing_the_etag_matches(self):
        """One entry of the list matching is enough."""
        assert if_none_match_matches('"x", "abc123", "y"', ETAG) is True

    def test_comma_separated_list_without_the_etag_does_not_match(self):
        """A list that does not contain the ETag is a non-match."""
        assert if_none_match_matches('"x", "y"', ETAG) is False

    def test_whitespace_around_list_entries_is_ignored(self):
        """Entries are compared after trimming, per RFC 9110 entity-tag lists."""
        assert if_none_match_matches('" x "', ETAG) is False
        assert if_none_match_matches('"abc123" , "y"', ETAG) is True
