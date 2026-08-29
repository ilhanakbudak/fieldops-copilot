"""The retrieval pipeline.

    analyse → (vector ∥ keyword) → fuse → rerank → expand → budget

Each step earns its place by fixing a failure the previous one has:

1. **Analyse.** Employees type context, not queries. Cheap model, and it cannot
   fail the request. It contributes a rewrite and the exact terms it pulled out
   of the question. Its guess about *document type* contributes nothing at all
   — see the note at the end of this docstring. Only the caller's role removes
   a document.
2. **Search both ways, in parallel.** Embeddings cannot tell `E-04` from `E-14`;
   lexical search cannot tell "warm water" from "elevated temperature". They are
   run concurrently because neither depends on the other and the slower one sets
   the latency either way.
3. **Fuse on ranks.** The two legs produce scores on incomparable scales.
   Agreement between them is the strongest signal available and neither can fake
   it alone.
4. **Rerank, then boost exact terms.** Fusion never reads the passages; a
   cross-encoder reads each against the question and is usually the largest
   single quality gain here. But it is a general-purpose relevance model and it
   does not know that `E-04` and `E-02` are different faults rather than
   near-synonyms — asked about one it will happily rank the other above it. A
   passage containing an extracted code verbatim is boosted, which is the whole
   reason query analysis pulls those codes out in the first place.
5. **Expand to parents.** Retrieval matched a precise 900-character chunk; the
   model reads the section around it, so it has the sentence that said which
   valve was under discussion.
6. **Budget.** A hard character cap. Without one, a question that happens to
   match a long table quietly costs ten times what a normal question does.

Role filtering is not a step. It is an argument threaded through every query
that touches the corpus, because a step can be reordered and an argument cannot
— see docs/SECURITY.md.

And it is the *only* thing that removes a document. An earlier version also
passed the analysis step's document-type guess into both search legs as a hard
filter, which was wrong in a way that took a failing test to notice: asked "why
is the water warm since the radon system was installed", the cheap model saw
"installed", guessed `sop`, and the service manual that actually answers the
question was excluded before ranking ever ran.

The obvious repair — keep the guess, demote it from a filter to a tiebreak
below the reranker's score — was tried and is not in this file either. It broke
the same test, for the same reason: with the SOP and the radon manual scored
close together, a tiebreak is a filter with extra steps. `doc_type` reaches the
ranker on every `SearchHit` and the ranker ignores it, which is a deliberate
outcome rather than an oversight. The guess is kept because the retrieval
inspector displays it, and because a future version that files documents by
type in the UI will want it. It does not touch the ordering.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_usage
from app.auth.rbac import Principal
from app.config import Settings, get_settings
from app.db.models import Chunk, Document
from app.llm.base import LlmProvider, Usage
from app.llm.pricing import cost_usd
from app.rag.analyse import QueryAnalysis, analyse_query
from app.rag.embed import EmbeddingProvider, get_embedding_provider
from app.rag.search.fusion import FusedHit, reciprocal_rank_fusion
from app.rag.search.keyword import keyword_search
from app.rag.search.rerank import Reranker, get_reranker
from app.rag.store import vector_store_for
from app.rag.store.base import SearchHit

logger = logging.getLogger("fieldops.rag.pipeline")


@dataclass(frozen=True, slots=True)
class Passage:
    """One retrieved section, as the prompt will see it.

    `marker` is the label the model is told to cite. Assigned here, at the point
    the passage is selected, so nothing downstream has to invent a mapping
    between what the model wrote and what was actually retrieved.
    """

    marker: str
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    page: int | None
    section: str | None
    score: float
    # Where each leg ranked it. Useful the moment a result looks wrong.
    ranks: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    analysis: QueryAnalysis
    passages: list[Passage]
    candidates: int
    duration_ms: int
    reranker: str

    @property
    def usage(self) -> Usage:
        return self.analysis.usage


async def retrieve(
    session: AsyncSession,
    question: str,
    principal: Principal,
    *,
    settings: Settings | None = None,
    llm: LlmProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    reranker: Reranker | None = None,
    top_k: int | None = None,
) -> RetrievalResult:
    settings = settings or get_settings()
    embedder = embedder or get_embedding_provider()
    reranker = reranker or get_reranker()
    top_k = top_k or settings.retrieval_top_k
    started = time.perf_counter()

    analysis = (
        await analyse_query(question, llm)
        if llm is not None
        else QueryAnalysis(original=question, rewritten=question, analysed=False)
    )

    # Recorded here, at the cheap model, rather than folded into whatever the
    # caller ends up spending. AD-5's claim is that a cheap model does the
    # analysis and the expensive one only the final answer; a dashboard that
    # bills both to `chat` at the chat model's price cannot show that, and the
    # first thing anybody asks a cost dashboard is which half is the money.
    if llm is not None and analysis.analysed and analysis.usage.total:
        await record_usage(
            feature="query_analysis",
            provider=llm.name,
            model=llm.cheap_model,
            input_tokens=analysis.usage.input_tokens,
            output_tokens=analysis.usage.output_tokens,
            cached_input_tokens=analysis.usage.cached_input_tokens,
            cost_usd=cost_usd(llm.cheap_model, analysis.usage),
        )

    hits = await _search(session, analysis, principal, settings, embedder)
    fused = reciprocal_rank_fusion(hits, limit=settings.retrieval_candidates)

    ranked = _rerank(analysis.search_text, fused, reranker, analysis)
    passages = await _expand(session, ranked[:top_k], settings)

    return RetrievalResult(
        analysis=analysis,
        passages=passages,
        candidates=len(fused),
        duration_ms=int((time.perf_counter() - started) * 1000),
        reranker=reranker.name,
    )


async def _search(
    session: AsyncSession,
    analysis: QueryAnalysis,
    principal: Principal,
    settings: Settings,
    embedder: EmbeddingProvider,
) -> dict[str, list[SearchHit]]:
    roles = principal.document_roles
    limit = settings.retrieval_candidates

    store = vector_store_for(session, settings)

    async def vector() -> list[SearchHit]:
        # Embedding is CPU-bound; off the event loop so the keyword query is not
        # waiting behind it.
        query_vector = await asyncio.to_thread(embedder.embed_query, analysis.search_text)
        return await store.search(query_vector, roles=roles, limit=limit)

    async def keyword() -> list[SearchHit]:
        return await keyword_search(session, analysis.search_text, roles=roles, limit=limit)

    # One SQLAlchemy session is not safe for concurrent statements, so the two
    # legs are awaited in sequence rather than gathered. The honest note: on
    # Postgres this could be two sessions and genuinely parallel, and at this
    # corpus size the keyword leg costs single-digit milliseconds — so the
    # complexity is not yet worth it.
    results = {"vector": await vector(), "keyword": await keyword()}

    logger.debug(
        "retrieval candidates: vector=%d keyword=%d",
        len(results["vector"]),
        len(results["keyword"]),
    )
    return results


# A passage containing the exact code the question asked about sorts ahead of
# every passage that does not, whatever the cross-encoder thought. Applied as a
# separate sort key rather than as an additive bonus, because the reranker's
# scores are unbounded logits and any constant large enough to be decisive for
# one model is wrong for the next.
def _rerank(
    query: str, fused: list[FusedHit], reranker: Reranker, analysis: QueryAnalysis
) -> list[FusedHit]:
    if not fused:
        return []

    scores = reranker.score(query, [item.hit.content for item in fused])
    if len(scores) != len(fused):
        logger.warning("reranker returned %d scores for %d passages", len(scores), len(fused))
        return fused

    exact = [_exact_matches(item.hit.content, analysis.keywords) for item in fused]

    order = sorted(range(len(fused)), key=lambda index: (-exact[index], -scores[index]))
    return [
        FusedHit(hit=fused[index].hit, score=scores[index], ranks=fused[index].ranks)
        for index in order
    ]


def _exact_matches(content: str, keywords: list[str]) -> int:
    """How many of the question's exact terms this passage actually contains.

    Case-insensitive substring, not a token match: `E-04` has to be found inside
    `### E-04 Brine Valve Fault` and must not be found inside `E-04` written as
    part of `E-042`. The word boundary is handled by the extraction pattern,
    which only ever produces whole codes.
    """
    if not keywords:
        return 0
    lowered = content.lower()
    return sum(1 for keyword in keywords if keyword.lower() in lowered)


async def _expand(
    session: AsyncSession, ranked: list[FusedHit], settings: Settings
) -> list[Passage]:
    """Replace each matched chunk with the section it belongs to.

    Retrieval matched a small chunk for precision. The model reads the parent,
    because the chunk that says "replace part NG-BV-14" is useless without the
    sentence above it naming the fault.

    De-duplicated by parent: two chunks from the same section produce one
    passage, or the model reads the same text twice and the context budget pays
    for it.
    """
    if not ranked:
        return []

    wanted = [(item.hit.document_id, item.hit.parent_index) for item in ranked]
    parents = await _load_parents(session, wanted)

    passages: list[Passage] = []
    seen: set[tuple[str, int | None]] = set()
    budget = settings.context_char_budget

    for item in ranked:
        key = (item.hit.document_id, item.hit.parent_index)
        if key in seen:
            continue
        seen.add(key)

        content = parents.get(key) or item.hit.content
        if len(content) > budget:
            # The matched chunk always fits and is always the more relevant of
            # the two, so a parent that blows the remaining budget falls back to
            # it rather than being truncated mid-sentence.
            content = item.hit.content
        if len(content) > budget:
            break

        budget -= len(content)
        passages.append(
            Passage(
                marker=f"S{len(passages) + 1}",
                chunk_id=item.hit.chunk_id,
                document_id=item.hit.document_id,
                document_title=item.hit.document_title,
                content=content,
                page=item.hit.page,
                section=item.hit.section,
                score=item.score,
                ranks=item.ranks,
            )
        )

    return passages


async def _load_parents(
    session: AsyncSession, wanted: list[tuple[str, int | None]]
) -> dict[tuple[str, int | None], str]:
    """Fetch every sibling chunk of the matched ones, in one query.

    One query rather than one per passage: six round trips to save a `WHERE IN`
    is the kind of thing that looks harmless until the corpus is real.
    """
    document_ids = {document_id for document_id, _ in wanted}
    indices = {index for _, index in wanted if index is not None}
    if not document_ids or not indices:
        return {}

    rows = (
        await session.execute(
            select(Chunk.document_id, Chunk.parent_index, Chunk.ordinal, Chunk.content)
            .where(Chunk.document_id.in_(document_ids), Chunk.parent_index.in_(indices))
            .order_by(Chunk.document_id, Chunk.parent_index, Chunk.ordinal)
        )
    ).all()

    grouped: dict[tuple[str, int | None], list[str]] = {}
    for document_id, parent_index, _ordinal, content in rows:
        grouped.setdefault((document_id, parent_index), []).append(content)

    return {key: _stitch(parts) for key, parts in grouped.items()}


def _stitch(parts: list[str]) -> str:
    """Join overlapping chunks without repeating the shared text.

    Chunks overlap by design so a fact split across a boundary stays retrievable
    from either side. Concatenating them naively makes the model read the same
    sentence twice and treat the repetition as emphasis.
    """
    if not parts:
        return ""

    joined = parts[0]
    for part in parts[1:]:
        limit = min(len(joined), len(part))
        for size in range(limit, 20, -1):
            if joined.endswith(part[:size]):
                joined += part[size:]
                break
        else:
            joined += "\n\n" + part
    return joined


async def document_titles(session: AsyncSession, document_ids: set[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(Document.id, Document.title).where(Document.id.in_(document_ids))
        )
    ).all()
    return {row[0]: row[1] for row in rows}
