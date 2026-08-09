"""Tests for reciprocal rank fusion (hybrid-recall, R4)."""

from backend.memory.retrieval.rrf import reciprocal_rank_fusion


def _key(item):
    return str(item.get("id", ""))


class TestReciprocalRankFusion:
    """Fusion, dedup, and ordering across ranked lists."""

    def test_item_in_both_lists_ranks_highest(self):
        """S4.1 — a record ranked by both sources beats single-source ones."""
        fts = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        vector = [{"id": "c"}, {"id": "d"}, {"id": "a"}]
        fused = reciprocal_rank_fusion([fts, vector], key=_key)
        ids = [item["id"] for item in fused]
        # "a" (rank 0 + rank 2) and "c" (rank 2 + rank 0) appear in both;
        # both outrank single-source "b"/"d".
        assert ids[0] in {"a", "c"}
        assert ids[1] in {"a", "c"}
        assert set(ids) == {"a", "b", "c", "d"}  # deduped union

    def test_vector_only_item_included(self):
        """S4.2 — a record found only by the vector source is kept."""
        fts = [{"id": "a"}]
        vector = [{"id": "z"}]
        ids = [item["id"] for item in reciprocal_rank_fusion([fts, vector], key=_key)]
        assert "z" in ids

    def test_empty_vector_preserves_fts_order(self):
        """S4.3 — with no vector hits the FTS order is preserved."""
        fts = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        fused = reciprocal_rank_fusion([fts, []], key=_key)
        assert [item["id"] for item in fused] == ["a", "b", "c"]

    def test_dedup_keeps_first_representative(self):
        """The first-seen dict for an id is the one returned."""
        fts = [{"id": "a", "summary": "from-fts"}]
        vector = [{"id": "a", "summary": "from-vector"}]
        fused = reciprocal_rank_fusion([fts, vector], key=_key)
        assert len(fused) == 1
        assert fused[0]["summary"] == "from-fts"
