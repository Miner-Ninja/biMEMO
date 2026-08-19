"""Tests for sqlite-vec vector storage and similarity search.

Covers extension loading, table creation, embedding storage,
and similarity search operations.
"""

import secrets

import pytest

from backend.memory.retrieval.vector_store import (
    create_vector_table,
    enable_vector_extension,
    search_similar,
    store_embedding,
)
from backend.security.database import DatabaseManager
from backend.security.schema import initialize_schema


@pytest.fixture
def vec_db(tmp_path):
    """Create a database with sqlite-vec extension loaded."""
    db = DatabaseManager(str(tmp_path / "vec.db"))
    db.open(bytearray(secrets.token_bytes(32)))
    initialize_schema(db)
    enable_vector_extension(db)
    create_vector_table(db, 4)
    yield db
    db.close()


class TestEnableExtension:
    """Tests for sqlite-vec extension loading."""

    def test_extension_loads_successfully(self, tmp_path):
        """Verify sqlite-vec extension loads without error."""
        db = DatabaseManager(str(tmp_path / "ext.db"))
        db.open(bytearray(secrets.token_bytes(32)))
        enable_vector_extension(db)
        db.close()

    def test_extension_requires_open_db(self, tmp_path):
        """Verify extension loading fails on closed database."""
        db = DatabaseManager(str(tmp_path / "closed.db"))
        with pytest.raises(RuntimeError, match="must be open"):
            enable_vector_extension(db)


class TestStoreEmbedding:
    """Tests for embedding storage."""

    def test_store_and_retrieve(self, vec_db):
        """Verify stored embedding can be found via search."""
        store_embedding(vec_db, "obs-1", [1.0, 0.0, 0.0, 0.0])
        results = search_similar(vec_db, [1.0, 0.0, 0.0, 0.0], 5)
        assert len(results) == 1
        assert results[0][0] == "obs-1"


class TestSearchSimilar:
    """Tests for similarity search."""

    def test_returns_closest_match(self, vec_db):
        """Verify search returns closest embedding first."""
        store_embedding(vec_db, "obs-a", [1.0, 0.0, 0.0, 0.0])
        store_embedding(vec_db, "obs-b", [0.0, 1.0, 0.0, 0.0])
        results = search_similar(vec_db, [0.9, 0.1, 0.0, 0.0], 2)
        assert results[0][0] == "obs-a"

    def test_respects_limit(self, vec_db):
        """Verify search respects the limit parameter."""
        for i in range(5):
            store_embedding(vec_db, f"obs-{i}", [float(i), 0.0, 0.0, 0.0])
        results = search_similar(vec_db, [2.0, 0.0, 0.0, 0.0], 3)
        assert len(results) == 3

    def test_empty_table_returns_empty(self, vec_db):
        """Verify search on empty table returns no results."""
        results = search_similar(vec_db, [1.0, 0.0, 0.0, 0.0], 5)
        assert len(results) == 0


class TestCosineMetric:
    """The vector table compares by angle, not magnitude (cosine)."""

    def test_ranking_ignores_magnitude(self, vec_db):
        """A long same-direction vector beats a short off-direction one.

        Under the old L2 default the short off-direction vector would
        rank first; cosine must rank by direction.
        """
        store_embedding(vec_db, "same-direction-long", [10.0, 0.0, 0.0, 0.0])
        store_embedding(vec_db, "off-direction-short", [0.9, 0.5, 0.0, 0.0])
        results = search_similar(vec_db, [1.0, 0.0, 0.0, 0.0], 2)
        assert results[0][0] == "same-direction-long"

    def test_metric_recorded_in_metadata(self, vec_db):
        """set_embedding_metadata pins the metric for migration checks."""
        from backend.memory.retrieval.vector_store import (
            VECTOR_DISTANCE_METRIC,
            get_embedding_metric,
            set_embedding_metadata,
        )

        assert get_embedding_metric(vec_db) is None
        set_embedding_metadata(vec_db, "test-model", 4)
        assert get_embedding_metric(vec_db) == VECTOR_DISTANCE_METRIC


class TestMetricMigration:
    """init_vector_table rebuilds tables created before the metric pin."""

    @pytest.mark.asyncio
    async def test_premetric_table_is_recreated(self, tmp_path):
        """Same model/dim but missing metric metadata forces a rebuild."""
        from backend.memory.retrieval.fulltext import create_fts_tables
        from backend.memory.retrieval.vector_store import (
            EMBEDDING_METRIC_METADATA_KEY,
        )
        from backend.processes.db_proxy.handlers.memory import (
            make_init_vector_table_handler,
        )

        db = DatabaseManager(str(tmp_path / "migrate.db"))
        db.open(bytearray(secrets.token_bytes(32)))
        initialize_schema(db)
        create_fts_tables(db)
        enable_vector_extension(db)
        handler = make_init_vector_table_handler(db)
        params = {"embedding_model": "m", "dimensions": 4}

        first = await handler(dict(params))
        assert first["recreated"] is True
        second = await handler(dict(params))
        assert second["recreated"] is False

        # Simulate a pre-metric database: erase the stored metric only.
        db.execute(
            "DELETE FROM metadata WHERE key = ?",
            (EMBEDDING_METRIC_METADATA_KEY,),
        )
        db.commit()
        third = await handler(dict(params))
        assert third["recreated"] is True
        db.close()
