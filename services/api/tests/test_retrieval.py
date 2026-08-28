"""The hybrid pipeline, end to end against a real corpus.

**What this file does and does not assert.** The suite runs with a deterministic
hashing embedder and a deterministic lexical reranker, so that an assertion
fails when the pipeline breaks rather than when a model has an opinion. That
buys reliability and it costs the ability to assert *ranking quality*, which is
a property of the real models.

So ranking quality is measured, not asserted: `scripts/evaluate_retrieval.py`
scores recall@k and MRR against `fixtures/eval/retrieval.json` with the real
embedder and cross-encoder, and its `--compare` mode ablates each stage. The
numbers are in docs/RAG.md.

What is asserted here is everything that must hold whatever the models say:
recall, role isolation, the keyword leg's exact-term behaviour, the context
budget, and marker assignment.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal, Role
from app.config import get_settings
from app.core.ids import new_id
from app.llm import MockLlmProvider
from app.rag.corpus import seed_corpus
from app.rag.search.keyword import keyword_search
from app.rag.search.pipeline import retrieve
from app.rag.search.rerank import LexicalReranker


def _principal(role: Role) -> Principal:
    return Principal(
        user_id=new_id(),
        email=f"{role.value}@example.com",
        full_name=role.value.title(),
        role=role,
        session_id=new_id(),
    )


@pytest.fixture
async def corpus(db: AsyncSession) -> AsyncSession:
    await seed_corpus(db, settings=get_settings())
    return db


async def _retrieve(db: AsyncSession, question: str, role: Role, top_k: int = 4) -> list[str]:
    result = await retrieve(
        db,
        question,
        _principal(role),
        settings=get_settings(),
        llm=MockLlmProvider(),
        # Deterministic, so these assertions are about the pipeline rather than
        # about a cross-encoder's opinion on a given afternoon.
        reranker=LexicalReranker(),
        top_k=top_k,
    )
    return [passage.section or "" for passage in result.passages]


async def test_the_keyword_leg_ranks_an_exact_code_above_its_near_neighbour(
    corpus: AsyncSession,
) -> None:
    """`E-04` and `E-14` are two characters apart and nearly identical vectors.

    Asserted on the keyword leg specifically, because BM25 over FTS5 is
    deterministic — this is the component that solves the problem, and it is the
    reason retrieval is hybrid rather than pure vector search.
    """
    hits = await keyword_search(
        corpus, "error code E-04", roles=frozenset({Role.TECHNICIAN}), limit=10
    )

    sections = [hit.section for hit in hits]
    # The claim is the ordering, not the absolute rank: the section headed
    # "2. Error Codes" legitimately scores well on "error" and "code", and is
    # also a reasonable passage for this question.
    assert sections.index("E-04 Brine Valve Fault") < sections.index(
        "E-14 Reserve Capacity Exceeded"
    )
    assert "E-04 Brine Valve Fault" in sections[:3]


async def test_a_bare_code_matches_only_the_passages_that_contain_it(
    corpus: AsyncSession,
) -> None:
    """What the tokeniser buys.

    `tokenchars '-_.'` keeps `E-14` as one term. The default tokeniser would
    split it into `e` and `14`, and `e` appears in every passage about every
    error code — so a search for one code would return all of them, ranked by
    length.
    """
    hits = await keyword_search(corpus, "E-14", roles=frozenset({Role.TECHNICIAN}), limit=10)

    assert [hit.section for hit in hits] == ["E-14 Reserve Capacity Exceeded"]


async def test_lexical_search_alone_cannot_separate_the_pair(corpus: AsyncSession) -> None:
    """Worth pinning down, because it is the limit of the keyword leg and the
    reason the pipeline does not stop there.

    The E-14 passage says "Distinct from E-04 despite the similar code", so it
    contains the exact term a search for `E-04` is looking for. BM25 then ranks
    the two within a rounding error of each other and prefers whichever passage
    is shorter. Separating them is the cross-encoder's job, measured by
    `scripts/evaluate_retrieval.py` rather than asserted here.
    """
    hits = await keyword_search(corpus, "E-04", roles=frozenset({Role.TECHNICIAN}), limit=10)
    scores = {hit.section: hit.score for hit in hits}

    assert "E-04 Brine Valve Fault" in scores
    assert "E-14 Reserve Capacity Exceeded" in scores
    assert abs(scores["E-04 Brine Valve Fault"] - scores["E-14 Reserve Capacity Exceeded"]) < 0.5


async def test_an_exact_term_boost_is_only_awarded_for_a_real_match(
    corpus: AsyncSession,
) -> None:
    """The cross-encoder is a general relevance model and does not know that
    E-04 and E-02 are different faults rather than near-synonyms. This is what
    overrules it."""
    from app.rag.search.pipeline import _exact_matches

    assert _exact_matches("### E-04 Brine Valve Fault", ["E-04"]) == 1
    assert _exact_matches("### E-02 Position Error", ["E-04"]) == 0
    assert _exact_matches("anything at all", []) == 0


async def test_a_question_with_no_code_still_finds_a_usable_section(
    corpus: AsyncSession,
) -> None:
    """The other half of hybrid search: nothing exact to match on.

    Note which section is expected. The radon *service manual* is written for
    technicians, so an office employee asking this cannot reach it — and should
    not. What they can reach is the handover step in the installation
    procedure, which is where the expectation was supposed to be set in the
    first place. That is the right answer for this person, not a degraded one.
    """
    sections = await _retrieve(
        corpus,
        "My customer says their water has been warm ever since the radon system went in",
        Role.OFFICE,
        top_k=6,
    )

    assert "5. Customer Handover" in sections


async def test_a_technician_asking_the_same_thing_reaches_the_manual(
    corpus: AsyncSession,
) -> None:
    sections = await _retrieve(
        corpus,
        "water has been warm ever since the radon aeration system was installed",
        Role.TECHNICIAN,
        top_k=6,
    )

    assert "2. Warm Water After Installation" in sections


async def test_pricing_never_reaches_a_technician(corpus: AsyncSession) -> None:
    """Not filtered out of the answer — never in the candidate set. A passage
    that reached the prompt has already leaked, whatever the UI renders."""
    result = await retrieve(
        corpus,
        "What is the dealer cost of a radon aeration system?",
        _principal(Role.TECHNICIAN),
        settings=get_settings(),
        llm=MockLlmProvider(),
        reranker=LexicalReranker(),
        top_k=10,
    )

    titles = {passage.document_title for passage in result.passages}
    assert "Dealer Price List and Margin Guide" not in titles
    assert all("dealer cost" not in passage.content.lower() for passage in result.passages)


async def test_the_same_question_reaches_pricing_for_sales(corpus: AsyncSession) -> None:
    """The boundary has to be a boundary, not a blanket refusal."""
    result = await retrieve(
        corpus,
        "What is the dealer cost of a radon aeration system?",
        _principal(Role.SALES),
        settings=get_settings(),
        llm=MockLlmProvider(),
        reranker=LexicalReranker(),
    )

    assert any(
        passage.document_title == "Dealer Price List and Margin Guide"
        for passage in result.passages
    )


async def test_an_admin_reads_every_audience(corpus: AsyncSession) -> None:
    """The same pricing question that a technician must not reach."""
    result = await retrieve(
        corpus,
        "What is the dealer cost of a radon aeration system?",
        _principal(Role.ADMIN),
        settings=get_settings(),
        llm=MockLlmProvider(),
        reranker=LexicalReranker(),
        top_k=10,
    )

    assert any(
        passage.document_title == "Dealer Price List and Margin Guide"
        for passage in result.passages
    )


async def test_keyword_search_alone_respects_the_role_filter(corpus: AsyncSession) -> None:
    """Asserted directly on the leg, because a filter applied in the pipeline
    and not in the query is a filter one refactor from being skipped."""
    hits = await keyword_search(
        corpus, "dealer cost radon", roles=frozenset({Role.TECHNICIAN}), limit=20
    )

    assert all(hit.document_title != "Dealer Price List and Margin Guide" for hit in hits)


async def test_keyword_search_finds_a_code_the_embedding_would_blur(
    corpus: AsyncSession,
) -> None:
    hits = await keyword_search(corpus, "E-04", roles=frozenset({Role.TECHNICIAN}), limit=10)

    assert hits
    assert any("E-04" in hit.content for hit in hits)


async def test_punctuation_in_a_question_does_not_break_the_query(
    corpus: AsyncSession,
) -> None:
    """A question mark reaching the FTS parser is a syntax error, and a search
    box that 500s on "?" is not a search box."""
    hits = await keyword_search(
        corpus,
        'what does "E-04" mean? (urgent) -- customer waiting',
        roles=frozenset({Role.TECHNICIAN}),
        limit=10,
    )

    assert hits


async def test_a_passage_never_returns_less_than_the_chunk_that_matched(
    corpus: AsyncSession,
) -> None:
    """The invariant that has to hold for every passage, whatever ranked first.

    Expansion can be an identity — a section short enough to be one chunk has
    no siblings to add, which is most sections in this small corpus. What it may
    never do is return *less* than what was matched.
    """
    from sqlalchemy import select

    from app.db.models import Chunk

    result = await retrieve(
        corpus,
        "brine valve fault salt bridge injector",
        _principal(Role.TECHNICIAN),
        settings=get_settings(),
        llm=MockLlmProvider(),
        reranker=LexicalReranker(),
        top_k=4,
    )

    assert result.passages
    for passage in result.passages:
        chunk = (
            await corpus.execute(select(Chunk).where(Chunk.id == passage.chunk_id))
        ).scalar_one()
        assert len(passage.content) >= len(chunk.content)


async def test_sibling_chunks_are_stitched_without_repeating_the_overlap(
    corpus: AsyncSession,
) -> None:
    """Chunks overlap by design so a fact split across a boundary is retrievable
    from either side. Concatenating them naively makes the model read the same
    sentence twice and treat the repetition as emphasis.

    Asserted on the stitching directly: which sections happen to span several
    chunks is a property of the corpus, not of the code under test.
    """
    from app.rag.search.pipeline import _stitch

    left = "Break the salt bridge with a broom handle, never with a metal bar."
    right = "never with a metal bar. Then inspect the injector screen."

    assert _stitch([left, right]) == (
        "Break the salt bridge with a broom handle, never with a metal bar. "
        "Then inspect the injector screen."
    )
    # No shared boundary: joined rather than fused, and nothing lost.
    assert _stitch(["First part.", "Unrelated second part."]) == (
        "First part.\n\nUnrelated second part."
    )


async def test_the_context_budget_is_respected(corpus: AsyncSession) -> None:
    """Without a cap, a question that happens to match a long table quietly
    costs ten times what a normal one does."""
    settings = get_settings().model_copy(update={"context_char_budget": 600})

    result = await retrieve(
        corpus,
        "installation procedure and warranty and error codes",
        _principal(Role.ADMIN),
        settings=settings,
        llm=MockLlmProvider(),
        reranker=LexicalReranker(),
        top_k=10,
    )

    assert sum(len(passage.content) for passage in result.passages) <= 600


async def test_markers_are_assigned_in_rank_order(corpus: AsyncSession) -> None:
    result = await retrieve(
        corpus,
        "What does error code E-04 mean?",
        _principal(Role.TECHNICIAN),
        settings=get_settings(),
        llm=MockLlmProvider(),
        reranker=LexicalReranker(),
        top_k=3,
    )

    assert [passage.marker for passage in result.passages] == ["S1", "S2", "S3"][
        : len(result.passages)
    ]
