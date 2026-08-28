"""Reciprocal rank fusion.

Two searches return two ranked lists with scores that mean completely different
things: a cosine similarity between 0 and 1, and a BM25 or `ts_rank_cd` value
with no bounded range at all. Normalising them onto a common scale requires
knowing each one's distribution, which changes with the corpus, the query and
the dialect.

RRF sidesteps that by throwing the scores away and keeping only the ranks:

    score(d) = Σ  1 / (k + rank(d, list))

A document near the top of both lists beats one that is first in one and absent
from the other, which is exactly the behaviour wanted — agreement between a
semantic match and a lexical match is strong evidence, and it is the only signal
here that neither method can fake on its own.

`k = 60` is the constant from the original paper and it is not arbitrary: it
flattens the difference between ranks 1 and 2 enough that a single list cannot
dominate, while still decaying fast enough that rank 30 contributes almost
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.rag.store.base import SearchHit

K = 60


@dataclass(frozen=True, slots=True)
class FusedHit:
    hit: SearchHit
    score: float
    # Which legs found it, and where. Kept because "the vector search found this
    # and the keyword search did not" is the first thing worth knowing when a
    # result looks wrong, and reconstructing it afterwards is impossible.
    ranks: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[SearchHit]], *, k: int = K, limit: int | None = None
) -> list[FusedHit]:
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    hits: dict[str, SearchHit] = {}

    for source, results in ranked_lists.items():
        for position, hit in enumerate(results, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + position)
            ranks.setdefault(hit.chunk_id, {})[source] = position
            # Keep the first instance seen. The rows are identical apart from
            # the score, which fusion is discarding anyway.
            hits.setdefault(hit.chunk_id, hit)

    fused = [
        FusedHit(hit=hits[chunk_id], score=score, ranks=ranks[chunk_id])
        for chunk_id, score in scores.items()
    ]
    # Tie-break on chunk id so the order is stable across runs. Without it, two
    # chunks with identical fusion scores swap places between requests and an
    # evaluation script measures noise.
    fused.sort(key=lambda item: (-item.score, item.hit.chunk_id))

    return fused[:limit] if limit else fused
