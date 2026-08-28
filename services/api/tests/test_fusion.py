"""Reciprocal rank fusion."""

from __future__ import annotations

from app.rag.search.fusion import reciprocal_rank_fusion
from app.rag.store.base import SearchHit


def _hit(chunk_id: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        document_id="doc",
        document_title="Doc",
        content=chunk_id,
        page=1,
        section=None,
        parent_index=0,
        score=0.0,
    )


def test_agreement_between_both_legs_wins() -> None:
    """The whole reason for running two searches: a passage both methods rank
    highly is stronger evidence than one either ranks first alone, and it is a
    signal neither can fake by itself."""
    fused = reciprocal_rank_fusion(
        {
            "vector": [_hit("both"), _hit("vector-only")],
            "keyword": [_hit("keyword-only"), _hit("both")],
        }
    )

    assert fused[0].hit.chunk_id == "both"


def test_incomparable_scores_are_discarded() -> None:
    """A cosine similarity and a BM25 value share no scale. Fusion keeps only
    the ranks, so a leg returning huge numbers cannot dominate."""
    huge = _hit("keyword-first")
    small = _hit("vector-first")

    fused = reciprocal_rank_fusion(
        {"vector": [small], "keyword": [huge]},
    )

    # Both are rank 1 in their own list, so both score identically.
    assert fused[0].score == fused[1].score


def test_every_leg_that_found_a_passage_is_recorded() -> None:
    """ "The vector search found this and the keyword search did not" is the
    first thing worth knowing when a result looks wrong, and it cannot be
    reconstructed afterwards."""
    fused = reciprocal_rank_fusion(
        {"vector": [_hit("a"), _hit("b")], "keyword": [_hit("b")]},
    )

    ranks = {item.hit.chunk_id: item.ranks for item in fused}
    assert ranks["b"] == {"vector": 2, "keyword": 1}
    assert ranks["a"] == {"vector": 1}


def test_ties_are_broken_deterministically() -> None:
    """Without a stable tie-break, two equal passages swap places between runs
    and an evaluation script measures noise."""
    first = reciprocal_rank_fusion({"vector": [_hit("b"), _hit("a")]})
    second = reciprocal_rank_fusion({"vector": [_hit("b"), _hit("a")]})

    assert [item.hit.chunk_id for item in first] == [item.hit.chunk_id for item in second]


def test_an_empty_leg_contributes_nothing() -> None:
    fused = reciprocal_rank_fusion({"vector": [_hit("a")], "keyword": []})

    assert [item.hit.chunk_id for item in fused] == ["a"]
