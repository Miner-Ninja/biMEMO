"""Full-text search using SQLite FTS5 extension.

Provides full-text indexing and keyword search on messages,
records, and summaries within the SQLCipher database.
FTS5 is built into SQLite and does not require an external
extension.

Snowball stemming is applied at both index and query time so that
different word forms (e.g. "программирования" / "программировании")
map to the same stem and match correctly.
"""

import logging
import re
from typing import TYPE_CHECKING

import snowballstemmer


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


logger = logging.getLogger(__name__)

# Characters with special meaning in FTS5 query syntax
_FTS5_SPECIAL = re.compile(r"[^\w\s]", re.UNICODE)

# Detect Cyrillic characters to choose Russian vs English stemmer
_CYRILLIC_RE = re.compile(r"[\u0430-\u044f\u0451\u0410-\u042f\u0401]")

# Snowball stemmers (pure Python, no compilation needed)
_STEMMER_RU = snowballstemmer.stemmer("russian")
_STEMMER_EN = snowballstemmer.stemmer("english")

# Current stemmer/index schema version — bump to force FTS rebuild on upgrade
_FTS_STEMMER_VERSION = 2

# Minimum stem length for query tokens (filters noise like "e", "mc" from "E=mc²")
MIN_STEM_LENGTH = 3

# Common Russian/English stop words to exclude from FTS queries
_STOP_WORDS = frozenset(
    {
        # Russian
        "я",
        "мы",
        "ты",
        "вы",
        "он",
        "она",
        "оно",
        "они",
        "мне",
        "мой",
        "моя",
        "моё",
        "мои",
        "наш",
        "ваш",
        "в",
        "на",
        "\u0441",
        "к",
        "\u0443",
        "\u043e",
        "из",
        "за",
        "по",
        "от",
        "до",
        "и",
        "\u0430",
        "но",
        "или",
        "да",
        "не",
        "ни",
        "бы",
        "что",
        "как",
        "где",
        "когда",
        "кто",
        "чем",
        "для",
        "это",
        "то",
        "все",
        "всё",
        "есть",
        "был",
        "была",
        "были",
        "будет",
        "ещё",
        "уже",
        "тоже",
        "очень",
        "так",
        "вот",
        "ли",
        "какой",
        "какая",
        "какое",
        "какие",
        "какую",
        "больше",
        "меньше",
        "всего",
        "самый",
        "самая",
        # English
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "my",
        "your",
        "his",
        "her",
        "its",
        "our",
        "their",
        "and",
        "or",
        "but",
        "not",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
    }
)

_FTS_SEARCH_SQL = {
    "messages_fts": (
        "SELECT rowid, rank, snippet(messages_fts, 0, '', '', '...', 32) "
        "FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?"
    ),
    "records_fts": (
        "SELECT rowid, rank, snippet(records_fts, 0, '', '', '...', 32) "
        "FROM records_fts WHERE records_fts MATCH ? ORDER BY rank LIMIT ?"
    ),
    "summaries_fts": (
        "SELECT rowid, rank, snippet(summaries_fts, 0, '', '', '...', 32) "
        "FROM summaries_fts WHERE summaries_fts MATCH ? ORDER BY rank LIMIT ?"
    ),
}

_FTS_DELETE_SQL = {
    "messages_fts": "DELETE FROM messages_fts WHERE rowid = ?",
    "records_fts": "DELETE FROM records_fts WHERE rowid = ?",
    "summaries_fts": "DELETE FROM summaries_fts WHERE rowid = ?",
    "entities_fts": "DELETE FROM entities_fts WHERE rowid = ?",
    "assertions_fts": "DELETE FROM assertions_fts WHERE rowid = ?",
}

_FTS_CLEAR_SQL = (
    "DELETE FROM messages_fts",
    "DELETE FROM records_fts",
    "DELETE FROM summaries_fts",
    "DELETE FROM entities_fts",
    "DELETE FROM assertions_fts",
)

_FTS_INSERT_SQL = {
    "messages_fts": "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
    "records_fts": (
        "INSERT INTO records_fts (rowid, summary, key_facts) VALUES (?, ?, ?)"
    ),
    "summaries_fts": (
        "INSERT INTO summaries_fts (rowid, main_point, based_on) VALUES (?, ?, ?)"
    ),
    "entities_fts": (
        "INSERT INTO entities_fts (rowid, canonical_name, aliases) VALUES (?, ?, ?)"
    ),
    "assertions_fts": (
        "INSERT INTO assertions_fts (rowid, assertion_text) VALUES (?, ?)"
    ),
}

_FTS_TABLE_CREATE_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
    "USING fts5(content, content_rowid='rowid')",
    "CREATE VIRTUAL TABLE IF NOT EXISTS records_fts "
    "USING fts5(summary, key_facts, content_rowid='rowid')",
    "CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts "
    "USING fts5(canonical_name, aliases, content_rowid='rowid')",
    "CREATE VIRTUAL TABLE IF NOT EXISTS assertions_fts "
    "USING fts5(assertion_text, content_rowid='rowid')",
)

_FTS_REBUILD_SOURCES = (
    (
        "messages_fts",
        "SELECT rowid, content FROM messages",
    ),
    (
        "records_fts",
        "SELECT rowid, summary, key_facts FROM records",
    ),
    (
        "summaries_fts",
        "SELECT rowid, main_point, based_on FROM summaries",
    ),
)


def _stem_token(token: str) -> str:
    """Stem one token with the matching language stemmer."""
    lower = token.lower()
    if _CYRILLIC_RE.search(lower):
        return _STEMMER_RU.stemWord(lower)
    return _STEMMER_EN.stemWord(lower)


def _fts_match_rows(
    db: DatabaseManager,
    *,
    table: str,
    query: str,
    limit: int,
) -> list[tuple]:
    """Run one FTS MATCH query and return raw rows."""
    safe_query = sanitize_fts_query(query)
    if not safe_query:
        return []
    cursor = db.execute(
        _FTS_SEARCH_SQL[table],
        (safe_query, limit),
    )
    return cursor.fetchall()


def _delete_index_row(db: DatabaseManager, *, table: str, rowid: int) -> None:
    """Delete one row from one FTS table."""
    db.execute(
        _FTS_DELETE_SQL[table],
        (rowid,),
    )


def _insert_stemmed_index_row(
    db: DatabaseManager,
    *,
    table: str,
    rowid: int,
    values: tuple[str, ...],
) -> None:
    """Insert one stemmed row into one FTS table."""
    db.execute(
        _FTS_INSERT_SQL[table],
        (rowid, *[stem_text(value) for value in values]),
    )


def _replace_index_row(
    db: DatabaseManager,
    *,
    table: str,
    rowid: int,
    values: tuple[str, ...],
) -> None:
    """Replace one FTS row so stale index state cannot survive reindexing."""
    _delete_index_row(db, table=table, rowid=rowid)
    _insert_stemmed_index_row(
        db,
        table=table,
        rowid=rowid,
        values=values,
    )


def _create_stable_fts_tables(db: DatabaseManager) -> None:
    """Create or recreate the FTS tables with the current schema."""
    for query in _FTS_TABLE_CREATE_SQL:
        db.execute(query)
    db.execute("DROP TABLE IF EXISTS summaries_fts")
    db.execute(
        "CREATE VIRTUAL TABLE summaries_fts "
        "USING fts5(main_point, based_on, content_rowid='rowid')"
    )
    db.commit()


def _clear_fts_tables(db: DatabaseManager) -> None:
    """Remove all indexed rows before a full rebuild."""
    for query in _FTS_CLEAR_SQL:
        db.execute(query)
    db.commit()


def _rebuild_index_table(
    db: DatabaseManager,
    *,
    table: str,
    select_query: str,
) -> None:
    """Rebuild one FTS table from its source rows."""
    cursor = db.execute(select_query)
    for row in cursor.fetchall():
        _insert_stemmed_index_row(
            db,
            table=table,
            rowid=row[0],
            values=tuple(str(value) for value in row[1:]),
        )


def stem_text(text: str) -> str:
    """Stem all tokens in text using Snowball (Russian/English).

    Language is detected per-token: tokens containing Cyrillic
    characters use the Russian stemmer, others use English.
    Tokens are lowercased before stemming.

    Args:
        text: Input text to stem.

    Returns:
        Space-joined stemmed tokens.
    """
    cleaned = _FTS5_SPECIAL.sub(" ", text)
    tokens = cleaned.split()
    return " ".join(_stem_token(token) for token in tokens)


def sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for safe use in FTS5 MATCH.

    Strips special characters, removes stop words, stems each
    token, and joins with OR for broad matching.

    Args:
        query: Raw user query string.

    Returns:
        Sanitized query safe for FTS5 MATCH, or empty string.
    """
    cleaned = _FTS5_SPECIAL.sub(" ", query)
    tokens = [t for t in cleaned.split() if t.lower() not in _STOP_WORDS]
    if not tokens:
        # Fallback: use all tokens if filtering removed everything
        tokens = cleaned.split()
    stemmed = [_stem_token(token) for token in tokens]
    stemmed = [s for s in stemmed if len(s) >= MIN_STEM_LENGTH]
    if not stemmed:
        return ""
    return " OR ".join(stemmed)


def create_fts_tables(db: DatabaseManager) -> None:
    """Create FTS5 virtual tables for memory content.

    On first run (or after stemmer version bump), rebuilds the
    FTS index from source tables so existing data gets stemmed.

    Args:
        db: An open DatabaseManager instance.
    """
    _create_stable_fts_tables(db)
    logger.info("FTS5 tables created")

    # Check stemmer version and rebuild if needed
    current_version = _get_stemmer_version(db)
    if current_version < _FTS_STEMMER_VERSION:
        logger.info(
            "FTS stemmer version %d < %d, rebuilding index",
            current_version,
            _FTS_STEMMER_VERSION,
        )
        rebuild_fts_index(db)
        _set_stemmer_version(db, _FTS_STEMMER_VERSION)


def _get_stemmer_version(db: DatabaseManager) -> int:
    """Read current FTS stemmer version from metadata table.

    Args:
        db: An open DatabaseManager instance.

    Returns:
        Version number, or 0 if not set.
    """
    cursor = db.execute("SELECT value FROM metadata WHERE key = 'fts_stemmer_version'")
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _set_stemmer_version(db: DatabaseManager, version: int) -> None:
    """Write FTS stemmer version to metadata table.

    Args:
        db: An open DatabaseManager instance.
        version: Version number to store.
    """
    db.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("fts_stemmer_version", str(version)),
    )
    db.commit()


def rebuild_fts_index(db: DatabaseManager) -> None:
    """Clear and rebuild FTS5 indexes from source tables.

    Re-reads all messages, records, and summaries, applies
    Snowball stemming, and re-indexes into FTS5 tables.

    Args:
        db: An open DatabaseManager instance.
    """
    _clear_fts_tables(db)
    for table, select_query in _FTS_REBUILD_SOURCES:
        _rebuild_index_table(
            db,
            table=table,
            select_query=select_query,
        )

    db.commit()
    logger.info("FTS index rebuild complete")


def index_message(db: DatabaseManager, rowid: int, content: str) -> None:
    """Add a message to the full-text index.

    Content is stemmed before indexing so that queries with
    different word forms still match.

    Args:
        db: An open DatabaseManager instance.
        rowid: Row ID of the message.
        content: Message content to index.
    """
    # Older databases may keep stale FTS rows after legacy conversation deletes.
    # Replace the row so indexing repairs drift instead of failing.
    _replace_index_row(
        db,
        table="messages_fts",
        rowid=rowid,
        values=(content,),
    )


def index_observation(
    db: DatabaseManager, rowid: int, summary: str, key_facts: str
) -> None:
    """Add an record to the full-text index.

    Summary and key_facts are stemmed before indexing.

    Args:
        db: An open DatabaseManager instance.
        rowid: Row ID of the record.
        summary: Record summary to index.
        key_facts: JSON string of key facts to index.
    """
    _insert_stemmed_index_row(
        db,
        table="records_fts",
        rowid=rowid,
        values=(summary, key_facts),
    )


def index_reflection(
    db: DatabaseManager,
    rowid: int,
    main_point: str,
    based_on: str,
) -> None:
    """Add a summary to the full-text index.

    Main point and supporting reasons are stemmed before indexing.

    Args:
        db: An open DatabaseManager instance.
        rowid: Row ID of the summary.
        main_point: Summary main point to index.
        based_on: JSON string of supporting reasons to index.
    """
    _insert_stemmed_index_row(
        db,
        table="summaries_fts",
        rowid=rowid,
        values=(main_point, based_on),
    )


def search_messages(db: DatabaseManager, query: str, limit: int) -> list[tuple]:
    """Search messages by keyword.

    Args:
        db: An open DatabaseManager instance.
        query: FTS5 search query string.
        limit: Maximum number of results.

    Returns:
        List of matching (rowid, rank, content snippet) tuples.
        rank is a negative float (BM25: lower is more relevant).
    """
    return _fts_match_rows(
        db,
        table="messages_fts",
        query=query,
        limit=limit,
    )


def search_observations(db: DatabaseManager, query: str, limit: int) -> list[tuple]:
    """Search records by keyword.

    Args:
        db: An open DatabaseManager instance.
        query: FTS5 search query string.
        limit: Maximum number of results.

    Returns:
        List of matching (rowid, rank, summary snippet) tuples.
        rank is a negative float (BM25: lower is more relevant).
    """
    return _fts_match_rows(
        db,
        table="records_fts",
        query=query,
        limit=limit,
    )


def search_reflections(db: DatabaseManager, query: str, limit: int) -> list[tuple]:
    """Search summaries by keyword.

    Args:
        db: An open DatabaseManager instance.
        query: FTS5 search query string.
        limit: Maximum number of results.

    Returns:
        List of matching (rowid, rank, main_point snippet) tuples.
        rank is a negative float (BM25: lower is more relevant).
    """
    return _fts_match_rows(
        db,
        table="summaries_fts",
        query=query,
        limit=limit,
    )


def delete_message_index(db: DatabaseManager, rowid: int) -> None:
    """Remove a message from the full-text index.

    Args:
        db: An open DatabaseManager instance.
        rowid: Row ID of the message to remove.
    """
    _delete_index_row(db, table="messages_fts", rowid=rowid)


def delete_record_index(db: DatabaseManager, rowid: int) -> None:
    """Remove an record from the full-text index.

    Args:
        db: An open DatabaseManager instance.
        rowid: Row ID of the record to remove.
    """
    _delete_index_row(db, table="records_fts", rowid=rowid)


def delete_summary_index(db: DatabaseManager, rowid: int) -> None:
    """Remove a summary from the full-text index.

    Args:
        db: An open DatabaseManager instance.
        rowid: Row ID of the summary to remove.
    """
    _delete_index_row(db, table="summaries_fts", rowid=rowid)


def index_entity(
    db: DatabaseManager,
    rowid: int,
    canonical_name: str,
    aliases_json: str,
) -> None:
    """Add one entity to the full-text index (name and aliases stemmed)."""
    _replace_index_row(
        db,
        table="entities_fts",
        rowid=rowid,
        values=(canonical_name, aliases_json),
    )


def index_assertion(
    db: DatabaseManager,
    rowid: int,
    assertion_text: str,
) -> None:
    """Add one assertion to the full-text index (text stemmed)."""
    _replace_index_row(
        db,
        table="assertions_fts",
        rowid=rowid,
        values=(assertion_text,),
    )
