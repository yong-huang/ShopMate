"""Reciprocal Rank Fusion over ranked id lists (System Design 3.4).

RRF score for a document is ``sum(1 / (k + rank))`` across all ranking
systems, with 1-based ranks. It is rank-based, so it never compares raw
BM25 scores with cosine similarities directly — the reason the design
spec picks it.
"""

from typing import Union

Scored = tuple[str, float]

DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = DEFAULT_K
) -> list[Scored]:
    """Fuse ranked id lists into one list sorted by fused score (desc).

    Ties keep first-seen order, so the result is deterministic for a
    given input.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
