"""Tests for FTS5 full-text search on messages and records.

Covers table creation, indexing, search, deletion, Snowball stemming,
and morphological matching for messages, records, and summaries.
"""

import secrets

import pytest
from backend.memory.retrieval.fulltext import (
    create_fts_tables,
    delete_message_index,
    delete_record_index,
    delete_summary_index,
    index_message,
    index_observation,
    index_reflection,
    rebuild_fts_index,
    sanitize_fts_query,
    search_messages,
    search_observations,
    search_reflections,
    stem_text,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


@pytest.fixture
def fts_db(tmp_path):
    """Create a database with FTS5 tables."""
    db = DatabaseManager(str(tmp_path / "fts.db"))
    db.open(bytearray(secrets.token_bytes(32)))
    initialize_schema(db)
    create_fts_tables(db)
    yield db
    db.close()


class TestCreateFtsTables:
    """Tests for FTS5 table creation."""

    def test_messages_fts_created(self, fts_db):
        """Verify messages_fts table exists."""
        cursor = fts_db.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='messages_fts'"
        )
        assert cursor.fetchone()[0] == 1

    def test_records_fts_created(self, fts_db):
        """Verify records_fts table exists."""
        cursor = fts_db.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='records_fts'"
        )
        assert cursor.fetchone()[0] == 1

    def test_summaries_fts_created(self, fts_db):
        """Verify summaries_fts table exists."""
        cursor = fts_db.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='summaries_fts'"
        )
        assert cursor.fetchone()[0] == 1


class TestStemText:
    """Tests for Snowball stemming function."""

    def test_english_plural(self):
        """Verify English plural is stemmed to singular form."""
        result = stem_text("languages")
        assert result == stem_text("language")

    def test_english_verb_forms(self):
        """Verify English verb forms share the same stem."""
        assert stem_text("programming") == stem_text("programs")

    def test_russian_noun_cases(self):
        """Verify Russian noun case endings produce same stem."""
        # программирования (genitive) vs программировании (prepositional)
        assert stem_text("программирования") == stem_text("программировании")

    def test_russian_verb_conjugation(self):
        """Verify Russian regular verb conjugations share stem."""
        # читаю (1st person) vs читать (infinitive)
        assert stem_text("читаю") == stem_text("читать")

    def test_mixed_languages(self):
        """Verify mixed Russian/English text is stemmed per-token."""
        result = stem_text("Python программирование")
        tokens = result.split()
        assert len(tokens) == 2
        # English token stemmed with English stemmer
        assert tokens[0] == stem_text("Python").strip()
        # Russian token stemmed with Russian stemmer
        assert tokens[1] == stem_text("программирование").strip()

    def test_empty_string(self):
        """Verify empty input returns empty output."""
        assert stem_text("") == ""

    def test_case_insensitive(self):
        """Verify stemming is case-insensitive."""
        assert stem_text("Running") == stem_text("running")

    def test_special_chars_stripped(self):
        """Verify special characters are removed before stemming."""
        result = stem_text("hello, world!")
        assert "," not in result
        assert "!" not in result


class TestMessageIndexing:
    """Tests for message FTS5 indexing and search."""

    def test_index_and_search_message(self, fts_db):
        """Verify indexed message is found by search."""
        index_message(fts_db, 1, "hello world from zeroclaw")
        fts_db.commit()
        results = search_messages(fts_db, "zeroclaw", 10)
        assert len(results) > 0

    def test_search_returns_rank(self, fts_db):
        """Verify search results include BM25 rank as float."""
        index_message(fts_db, 1, "ranking test content")
        fts_db.commit()
        results = search_messages(fts_db, "ranking", 10)
        assert len(results) == 1
        _rowid, rank, _snippet = results[0]
        assert isinstance(rank, float)
        assert rank < 0  # FTS5 BM25 rank is negative

    def test_search_no_results(self, fts_db):
        """Verify search returns empty for non-matching query."""
        index_message(fts_db, 1, "hello world")
        fts_db.commit()
        results = search_messages(fts_db, "nonexistent", 10)
        assert len(results) == 0

    def test_delete_message_index(self, fts_db):
        """Verify deleted message is no longer found."""
        index_message(fts_db, 1, "deletable content")
        fts_db.commit()
        delete_message_index(fts_db, 1)
        fts_db.commit()
        results = search_messages(fts_db, "deletable", 10)
        assert len(results) == 0

    def test_index_message_repairs_stale_rowid(self, fts_db):
        """Verify re-indexing the same rowid repairs stale FTS entries."""
        index_message(fts_db, 1, "stale content")
        fts_db.commit()
        index_message(fts_db, 1, "fresh content")
        fts_db.commit()

        stale_results = search_messages(fts_db, "stale", 10)
        fresh_results = search_messages(fts_db, "fresh", 10)

        assert len(stale_results) == 0
        assert len(fresh_results) == 1


class TestRecordIndexing:
    """Tests for record FTS5 indexing and search."""

    def test_index_and_search_observation(self, fts_db):
        """Verify indexed record is found by search."""
        index_observation(fts_db, 1, "user discussed API design", "[]")
        fts_db.commit()
        results = search_observations(fts_db, "API", 10)
        assert len(results) > 0

    def test_record_search_returns_rank(self, fts_db):
        """Verify record search results include BM25 rank."""
        index_observation(fts_db, 1, "ranking record test", "[]")
        fts_db.commit()
        results = search_observations(fts_db, "ranking", 10)
        assert len(results) == 1
        _rowid, rank, _snippet = results[0]
        assert isinstance(rank, float)
        assert rank < 0

    def test_search_by_key_facts(self, fts_db):
        """Verify search works on key_facts field."""
        index_observation(
            fts_db,
            1,
            "summary here",
            '["important fact about deployment"]',
        )
        fts_db.commit()
        results = search_observations(fts_db, "deployment", 10)
        assert len(results) > 0

    def test_delete_record_index(self, fts_db):
        """Verify deleted record is no longer found."""
        index_observation(fts_db, 1, "removable summary", "[]")
        fts_db.commit()
        delete_record_index(fts_db, 1)
        fts_db.commit()
        results = search_observations(fts_db, "removable", 10)
        assert len(results) == 0


class TestSummaryIndexing:
    """Tests for summary FTS5 indexing and search."""

    def test_index_and_search_reflection(self, fts_db):
        """Verify indexed summary is found by search."""
        index_reflection(
            fts_db,
            1,
            "User prefers dark terminal themes",
            '["Prefers high-contrast code blocks"]',
        )
        fts_db.commit()
        results = search_reflections(fts_db, "terminal", 10)
        assert len(results) > 0

    def test_search_summary_by_based_on(self, fts_db):
        """Verify summary search works on the based_on field."""
        index_reflection(
            fts_db,
            1,
            "User values concise answers",
            '["Asked for shorter replies in review"]',
        )
        fts_db.commit()
        results = search_reflections(fts_db, "shorter", 10)
        assert len(results) > 0

    def test_delete_summary_index(self, fts_db):
        """Verify deleted summary is no longer found."""
        index_reflection(fts_db, 1, "Removable summary", "[]")
        fts_db.commit()
        delete_summary_index(fts_db, 1)
        fts_db.commit()
        results = search_reflections(fts_db, "removable", 10)
        assert len(results) == 0


class TestSanitizeFtsQuery:
    """Tests for FTS5 query sanitization."""

    def test_plain_text_or_joined(self):
        """Verify plain text tokens are stemmed and OR-joined."""
        result = sanitize_fts_query("hello world")
        assert "OR" in result

    def test_question_mark_stripped(self):
        """Verify ? is removed and stop words filtered."""
        result = sanitize_fts_query("Что я люблю есть?")
        assert "?" not in result
        # "люблю" is stemmed — verify it produced a non-empty stem
        assert len(result) > 0

    def test_special_chars_stripped(self):
        """Verify FTS5 operators are removed."""
        result = sanitize_fts_query('hello + world * "test"')
        assert "+" not in result
        assert "*" not in result
        assert '"' not in result
        assert "OR" in result

    def test_empty_after_sanitization(self):
        """Verify all-special query becomes empty."""
        assert sanitize_fts_query("???") == ""

    def test_whitespace_collapsed(self):
        """Verify multiple spaces collapsed and OR-joined."""
        result = sanitize_fts_query("  hello   world  ")
        assert "OR" in result

    def test_search_with_special_chars_no_crash(self, fts_db):
        """Verify search with special chars returns empty, not error."""
        index_message(fts_db, 1, "some content")
        fts_db.commit()
        results = search_messages(fts_db, "что?", 10)
        assert isinstance(results, list)

    def test_search_observations_with_special_chars(self, fts_db):
        """Verify record search with special chars returns empty."""
        index_observation(fts_db, 1, "some summary", "[]")
        fts_db.commit()
        results = search_observations(fts_db, "что?", 10)
        assert isinstance(results, list)

    def test_short_stems_filtered(self):
        """Verify single-char stems are filtered out."""
        # "a x y" — all stems are 1 char, filtered by MIN_STEM_LENGTH
        result = sanitize_fts_query("a x y")
        assert result == ""

    def test_mixed_short_and_long_stems(self):
        """Verify only long stems survive filtering."""
        result = sanitize_fts_query("E=mc² формула")
        assert result != ""
        # Should contain the stem of "формула" but not short stems
        assert "формул" in result

    def test_all_short_stems_return_empty(self):
        """Verify all-short-stem query returns empty string."""
        result = sanitize_fts_query("a b c")
        assert result == ""


class TestMorphologicalMatching:
    """Tests that different word forms match via stemming."""

    def test_russian_noun_cases_match(self, fts_db):
        """Verify Russian noun case variants find the same document."""
        # Index with genitive case
        index_observation(
            fts_db,
            1,
            "изучение программирования на Python",
            "[]",
        )
        fts_db.commit()
        # Search with prepositional case
        results = search_observations(fts_db, "программировании", 10)
        assert len(results) > 0, (
            "Prepositional 'программировании' should find genitive 'программирования'"
        )

    def test_russian_verb_forms_match(self, fts_db):
        """Verify Russian verb conjugations match."""
        index_observation(fts_db, 1, "я читаю книги каждый день", "[]")
        fts_db.commit()
        results = search_observations(fts_db, "читать", 10)
        assert len(results) > 0, "Infinitive 'читать' should find conjugated 'читаю'"

    def test_english_plural_singular_match(self, fts_db):
        """Verify English plural/singular match."""
        index_message(fts_db, 1, "learning multiple languages")
        fts_db.commit()
        results = search_messages(fts_db, "language", 10)
        assert len(results) > 0, "Singular 'language' should find plural 'languages'"

    def test_english_verb_forms_match(self, fts_db):
        """Verify English verb forms match."""
        index_message(fts_db, 1, "she was running fast")
        fts_db.commit()
        results = search_messages(fts_db, "run", 10)
        assert len(results) > 0, "'run' should find 'running'"

    def test_bilingual_document(self, fts_db):
        """Verify bilingual documents are searchable in both languages."""
        index_observation(
            fts_db,
            1,
            "backend разработчик пишет на Python",
            '["использует Python и Go"]',
        )
        fts_db.commit()
        # Search in Russian
        ru_results = search_observations(fts_db, "разработчики", 10)
        assert len(ru_results) > 0, (
            "Plural 'разработчики' should find singular 'разработчик'"
        )
        # Search in English
        en_results = search_observations(fts_db, "Python", 10)
        assert len(en_results) > 0


class TestRebuildFtsIndex:
    """Tests for FTS index rebuild from source tables."""

    def test_rebuild_reindexes_messages(self, fts_db):
        """Verify rebuild picks up messages from source table."""
        # Insert a message into source table (not FTS)
        fts_db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        fts_db.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
            ("m1", "c1", "user", "тестирование поиска"),
        )
        fts_db.commit()

        # Rebuild should pick it up
        rebuild_fts_index(fts_db)
        results = search_messages(fts_db, "тестирования", 10)
        assert len(results) > 0, "Rebuild should index messages from source table"

    def test_rebuild_reindexes_observations(self, fts_db):
        """Verify rebuild picks up records from source table."""
        fts_db.execute("INSERT INTO conversations (id) VALUES (?)", ("c1",))
        fts_db.execute(
            "INSERT INTO records "
            "(id, conversation_id, session_id, summary, key_facts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "o1",
                "c1",
                "s1",
                "пользователь любит программирование",
                '["Python разработчик"]',
            ),
        )
        fts_db.commit()

        rebuild_fts_index(fts_db)
        results = search_observations(fts_db, "программировании", 10)
        assert len(results) > 0, "Rebuild should index records from source table"

    def test_rebuild_clears_old_fts_data(self, fts_db):
        """Verify rebuild clears stale FTS entries."""
        # Index directly (bypassing source table)
        index_message(fts_db, 999, "stale data without source row")
        fts_db.commit()
        results = search_messages(fts_db, "stale", 10)
        assert len(results) > 0

        # Rebuild should clear it (no matching source row)
        rebuild_fts_index(fts_db)
        results = search_messages(fts_db, "stale", 10)
        assert len(results) == 0, "Rebuild should clear FTS entries without source rows"
