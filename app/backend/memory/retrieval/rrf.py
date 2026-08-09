"""Reciprocal Rank Fusion for merging heterogeneous ranked result lists.

RRF fuses several independently-ranked lists without needing to compare
their raw scores (BM25 rank vs cosine distance): each list contributes
1/(k + rank) per item, summed across lists, and items are de-duplicated
by a stable key. Standard k = 60.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict[str, Any]]],
    key: Callable[[dict[str, Any]], str],
    k: int = RRF_K,
) -> list[dict[str, Any]]:
    """Fuse ranked lists into one, de-duplicated and ordered by RRF score.

    Args:
        ranked_lists: Each inner list is already ordered best-first.
        key: Extracts the stable identity used to de-duplicate an item
            across lists (e.g. the record id).
        k: RRF damping constant; larger flattens rank influence.

    Returns:
        One list, each id once, ordered by descending fused score. The
        first-seen dict for an id is kept as the representative.
    """
    scores: dict[str, float] = {}
    representative: dict[str, dict[str, Any]] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            item_key = key(item)
            if not item_key:
                continue
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (k + rank + 1)
            representative.setdefault(item_key, item)
    ordered_keys = sorted(scores, key=lambda item_key: scores[item_key], reverse=True)
    return [representative[item_key] for item_key in ordered_keys]
