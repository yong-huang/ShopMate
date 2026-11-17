"""Tests for Reciprocal Rank Fusion (System Design 3.4, k=60).

Expected values are hand-computed literals, independent of the
implementation::

    rankings A = [d1, d2, d3], B = [d2, d4]
    score(d) = sum(1 / (k + rank)), rank starting at 1
    d1 = 1/61                        = 0.016393...
    d2 = 1/62 + 1/61                 = 0.032517...
    d3 = 1/63                        = 0.015873...
    d4 = 1/62                        = 0.016129...
    fused order: d2 > d1 > d4 > d3
"""

import math

from app.retrieval.fusion import reciprocal_rank_fusion


def test_fusion_interleaves_two_rankings_by_hand_computed_scores() -> None:
    fused = reciprocal_rank_fusion(
        [["d1", "d2", "d3"], ["d2", "d4"]], k=60
    )

    assert [doc_id for doc_id, _ in fused] == ["d2", "d1", "d4", "d3"]
    scores = dict(fused)
    assert math.isclose(scores["d2"], 1 / 62 + 1 / 61, rel_tol=1e-9)
    assert math.isclose(scores["d1"], 1 / 61, rel_tol=1e-9)
    assert math.isclose(scores["d4"], 1 / 62, rel_tol=1e-9)
    assert math.isclose(scores["d3"], 1 / 63, rel_tol=1e-9)


def test_fusion_single_ranking_matches_bm25_order() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"]], k=60)

    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]


def test_fusion_k_changes_the_spread() -> None:
    with_k60 = dict(reciprocal_rank_fusion([["a", "b", "c"]], k=60))
    with_k1 = dict(reciprocal_rank_fusion([["a", "b", "c"]], k=1))

    # Absolute scores shrink as k grows; the rank falloff steepens as k
    # shrinks (rank 1 vs 2 gap: 1/2 - 1/3 with k=1 vs 1/61 - 1/62 with k=60).
    assert with_k60["a"] < with_k1["a"]
    assert with_k1["a"] / with_k1["b"] > with_k60["a"] / with_k60["b"]
    assert math.isclose(with_k1["a"], 1 / 2, rel_tol=1e-9)
    assert math.isclose(with_k1["b"], 1 / 3, rel_tol=1e-9)
