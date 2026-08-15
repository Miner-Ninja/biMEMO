"""Tests for hybrid FTS+vector recall wiring (hybrid-recall, R3+R4).

Confirms memory_recall embeds the query and fuses vector hits when an
embedding callable is registered, and stays FTS+graph-only when not.
"""

import pytest
from backend.tools.handlers import registration
from backend.tools.handlers.registration import (
    _build_fast_recall_result,
    set_recall_embed_fn,
)


class _FakeRecallClient:
    """Minimal db client for the fast-recall path."""

    def __init__(self, record_hits, vector_hits):
        self._record_hits = record_hits
        self._vector_hits = vector_hits
        self.vector_calls = 0

    async def find_graph_entry_points(self, query, limit):
        del query, limit
        return []

    async def fulltext_search(self, *, query, table, limit):
        del query, limit
        return self._record_hits if table == "records" else []

    async def vector_search(self, query_embedding, limit):
        del query_embedding, limit
        self.vector_calls += 1
        return self._vector_hits


@pytest.fixture(autouse=True)
def _reset_embed_fn():
    """Each test controls the recall embed callable in isolation."""
    yield
    set_recall_embed_fn(None)


class TestHybridRecall:
    """Query embedding + vector fusion in the fast-recall path."""

    @pytest.mark.asyncio
    async def test_without_embed_fn_skips_vector(self):
        """S3.2 — no embed callable: vector_search is never called."""
        client = _FakeRecallClient(
            record_hits=[{"id": "r1", "summary": "fts only"}],
            vector_hits=[{"id": "rv", "summary": "vector only"}],
        )
        set_recall_embed_fn(None)
        result = await _build_fast_recall_result(
            db_client=client, query="anything", max_message_chars=200
        )
        assert client.vector_calls == 0
        assert "fts only" in result
        assert "vector only" not in result

    @pytest.mark.asyncio
    async def test_with_embed_fn_fuses_vector(self):
        """S3.1 + S4.2 — embed callable set: vector hit is fetched and shown."""
        client = _FakeRecallClient(
            record_hits=[{"id": "r1", "summary": "fts only"}],
            vector_hits=[{"id": "rv", "summary": "vector only"}],
        )

        async def fake_embed(text):
            del text
            return [0.1, 0.2, 0.3]

        set_recall_embed_fn(fake_embed)
        result = await _build_fast_recall_result(
            db_client=client, query="anything", max_message_chars=200
        )
        assert client.vector_calls == 1
        assert "vector only" in result  # semantic-only record surfaced
        assert "fts only" in result

    @pytest.mark.asyncio
    async def test_embed_failure_falls_back_to_fts(self):
        """A failing embedder degrades to FTS records, no crash."""
        client = _FakeRecallClient(
            record_hits=[{"id": "r1", "summary": "fts only"}],
            vector_hits=[{"id": "rv", "summary": "vector only"}],
        )

        async def boom(text):
            del text
            raise RuntimeError("embed endpoint down")

        set_recall_embed_fn(boom)
        result = await _build_fast_recall_result(
            db_client=client, query="anything", max_message_chars=200
        )
        assert client.vector_calls == 0
        assert "fts only" in result

    def test_setter_roundtrip(self):
        """The module holder stores and clears the callable."""
        marker = object()
        set_recall_embed_fn(marker)
        assert registration._recall_embed_fn is marker
        set_recall_embed_fn(None)
        assert registration._recall_embed_fn is None
