"""The semantic cache.

Most of what is asserted here is the cache *refusing*. A cache that answers is
easy; a cache that knows which questions it has no business answering, and which
askers it has no business answering them for, is the whole design.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal, Role
from app.core.ids import new_id
from app.db.models import CachedAnswer, UsageEvent
from app.llm import cache
from tests.conftest import login

TTL = timedelta(hours=24)


def _vector(*leading: float) -> list[float]:
    """A vector of the width the schema expects, with the interesting part at
    the front. The `Embedding` type rejects any other length — which is the
    column doing its job, and the reason these are not written by hand."""
    from app.db.models import EMBEDDING_DIM

    return [*leading, *([0.0] * (EMBEDDING_DIM - len(leading)))]


def _principal(role: Role) -> Principal:
    return Principal(
        user_id=new_id(),
        email=f"{role.value}@example.com",
        full_name=role.value,
        role=role,
        session_id=new_id(),
    )


async def _store(db: AsyncSession, principal: Principal, vector: list[float], answer: str) -> None:
    await cache.store(
        db,
        question="what is the warranty",
        embedding=vector,
        answer=answer,
        citations=[{"marker": "S1", "documentTitle": "Warranty Policy"}],
        principal=principal,
    )
    await db.commit()


class TestWhatMayBeCachedAtAll:
    """A cached answer has to be a function of the question. Most of this
    assistant's answers are not."""

    def test_a_knowledge_answer_may_be(self) -> None:
        assert cache.cacheable(tools_used=["search_knowledge_base"], has_history=False)

    @pytest.mark.parametrize(
        "tools",
        [
            # Wrong within a day.
            ["mcp_time_get_current_time"],
            # A balance and a bin count are current values, and a stale one read
            # aloud on the phone is worse than a slow one.
            ["find_customer", "get_customer_detail"],
            ["find_material"],
            # A search *and* a lookup is still a lookup.
            ["search_knowledge_base", "find_customer"],
        ],
    )
    def test_anything_that_touched_a_live_system_may_not_be(self, tools: list[str]) -> None:
        assert not cache.cacheable(tools_used=tools, has_history=False)

    def test_a_follow_up_may_not_be(self) -> None:
        """ "And how long does that last?" means something different in every
        conversation, so its answer is not a function of its own words."""
        assert not cache.cacheable(tools_used=["search_knowledge_base"], has_history=True)

    def test_an_answer_from_no_tool_at_all_may_not_be(self) -> None:
        """The model answered from the conversation or declined. There is no
        retrieval behind it to make it reproducible."""
        assert not cache.cacheable(tools_used=[], has_history=False)


class TestTheAudienceIsPartOfTheKey:
    """The decision the whole module is built around.

    A cached answer was assembled from the passages the *first* asker could see.
    Serving it to somebody else serves them a summary of documents they may have
    no right to — and it arrives with citations, looking exactly as trustworthy
    as an answer they were entitled to.
    """

    async def test_another_role_asking_the_identical_question_misses(
        self, db: AsyncSession
    ) -> None:
        vector = _vector(1.0)
        await _store(db, _principal(Role.SALES), vector, "Dealer cost is 2,400.")

        hit = await cache.lookup(
            db,
            question="what is the warranty",
            embedding=vector,
            principal=_principal(Role.TECHNICIAN),
            threshold=0.9,
            ttl=TTL,
        )

        assert hit is None

    async def test_the_same_role_hits(self, db: AsyncSession) -> None:
        vector = _vector(1.0)
        await _store(db, _principal(Role.SALES), vector, "Dealer cost is 2,400.")

        hit = await cache.lookup(
            db,
            question="what is the warranty",
            embedding=vector,
            principal=_principal(Role.SALES),
            threshold=0.9,
            ttl=TTL,
        )

        assert hit is not None
        assert hit.answer == "Dealer cost is 2,400."
        assert hit.citations == [{"marker": "S1", "documentTitle": "Warranty Policy"}]

    async def test_an_administrator_does_not_inherit_everyone_elses_entries(
        self, db: AsyncSession
    ) -> None:
        """An admin reads every audience, so their *own* answers are broader —
        which makes their entries a different key, not a superset that can be
        served from anybody's."""
        vector = _vector(1.0)
        await _store(db, _principal(Role.TECHNICIAN), vector, "Break the salt bridge.")

        hit = await cache.lookup(
            db,
            question="what is the warranty",
            embedding=vector,
            principal=_principal(Role.ADMIN),
            threshold=0.9,
            ttl=TTL,
        )

        assert hit is None


class TestNearness:
    async def test_a_rewording_hits(self, db: AsyncSession) -> None:
        principal = _principal(Role.OFFICE)
        await _store(db, principal, _vector(1.0), "Twelve months.")

        hit = await cache.lookup(
            db,
            question="what is the warranty",
            embedding=_vector(0.99, 0.14),
            principal=principal,
            threshold=0.95,
            ttl=TTL,
        )

        assert hit is not None
        assert hit.similarity >= 0.95

    async def test_a_neighbouring_question_does_not(self, db: AsyncSession) -> None:
        """The failure of a cache that is too eager is answering a question
        nobody asked, confidently and with citations."""
        principal = _principal(Role.OFFICE)
        await _store(db, principal, _vector(1.0), "Twelve months.")

        hit = await cache.lookup(
            db,
            question="what is the warranty",
            embedding=_vector(0.6, 0.8),
            principal=principal,
            threshold=0.95,
            ttl=TTL,
        )

        assert hit is None

    async def test_an_expired_entry_is_not_served(self, db: AsyncSession) -> None:
        principal = _principal(Role.OFFICE)
        await _store(db, principal, _vector(1.0), "Twelve months.")

        hit = await cache.lookup(
            db,
            question="what is the warranty",
            embedding=_vector(1.0),
            principal=principal,
            threshold=0.9,
            ttl=timedelta(seconds=0),
        )

        assert hit is None

    async def test_a_hit_counts_itself(self, db: AsyncSession) -> None:
        """`hits` is how an operator sees whether the cache is earning its
        keep, and which questions a business actually repeats."""
        principal = _principal(Role.OFFICE)
        await _store(db, principal, _vector(1.0), "Twelve months.")

        for _ in range(3):
            await cache.lookup(
                db,
                question="what is the warranty",
                embedding=_vector(1.0),
                principal=principal,
                threshold=0.9,
                ttl=TTL,
            )
        await db.commit()

        row = (await db.execute(select(CachedAnswer))).scalar_one()
        assert row.hits == 3
        assert row.last_hit_at is not None


class TestHousekeeping:
    async def test_expired_entries_are_swept(self, db: AsyncSession) -> None:
        principal = _principal(Role.OFFICE)
        await _store(db, principal, _vector(1.0), "Twelve months.")

        removed = await cache.purge(db, ttl=timedelta(seconds=0))
        await db.commit()

        assert removed == 1
        assert (await db.execute(select(CachedAnswer))).scalars().all() == []

    async def test_a_live_entry_survives_a_sweep(self, db: AsyncSession) -> None:
        principal = _principal(Role.OFFICE)
        await _store(db, principal, _vector(1.0), "Twelve months.")

        assert await cache.purge(db, ttl=TTL) == 0

    async def test_an_empty_answer_is_not_stored(self, db: AsyncSession) -> None:
        await cache.store(
            db,
            question="q",
            embedding=_vector(1.0),
            answer="   ",
            citations=[],
            principal=_principal(Role.OFFICE),
        )
        await db.commit()

        assert (await db.execute(select(CachedAnswer))).scalars().all() == []


class TestOverHttp:
    async def test_a_repeated_question_is_served_from_the_cache(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """End to end, and the assertion that matters is the *second* answer
        being identical to the first while costing nothing."""
        from tests.test_chat import _seed, ask

        await _seed(client)
        await login(client, "technician")

        first = await ask(client, "What does error code E-04 mean?")
        assert first["text"]

        stored = (await db.execute(select(CachedAnswer))).scalars().all()
        assert len(stored) == 1, "a citable knowledge answer was not cached"

        second = await ask(client, "What does error code E-04 mean?")
        assert second["done"]["cacheHit"] is True
        assert second["text"] == first["text"]
        assert second["done"]["usage"]["estimatedCostUsd"] == 0.0

    async def test_a_hit_is_still_recorded_as_usage(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Without this the dashboard shows spending fall and cannot say whether
        the cache is working or everybody stopped asking."""
        from tests.test_chat import _seed, ask

        await _seed(client)
        await login(client, "technician")
        await ask(client, "What does error code E-04 mean?")
        await ask(client, "What does error code E-04 mean?")

        events = list(
            (await db.execute(select(UsageEvent).where(UsageEvent.cache_hit.is_(True)))).scalars()
        )

        assert len(events) == 1
        assert events[0].cost_usd == 0.0
        assert events[0].input_tokens == 0

    async def test_a_hit_is_recorded_in_the_audit_trail_as_a_hit(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        from app.db.models import AuditEvent
        from tests.test_chat import _seed, ask

        await _seed(client)
        await login(client, "technician")
        await ask(client, "What does error code E-04 mean?")
        await ask(client, "What does error code E-04 mean?")

        details = [
            json.loads(event.detail or "{}")
            for event in (
                await db.execute(select(AuditEvent).where(AuditEvent.action == "ai.answer"))
            ).scalars()
        ]

        assert [detail.get("cacheHit") for detail in details] == [None, True]

    async def test_another_role_is_answered_afresh(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """The audience boundary, over HTTP. A technician's cached answer must
        not reach an administrator, whose audience is different."""
        from tests.test_chat import _seed, ask

        await _seed(client)
        await login(client, "technician")
        await ask(client, "What does error code E-04 mean?")

        await login(client, "admin")
        second = await ask(client, "What does error code E-04 mean?")

        assert not second["done"].get("cacheHit")
        keys = {
            row.audience_key for row in (await db.execute(select(CachedAnswer))).scalars().all()
        }
        assert keys == {"technician", "admin,office,sales,technician"}

    async def test_a_declined_question_is_not_cached(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Storing "I could not find anything" would make the cache good at
        repeating a failure."""
        from tests.test_chat import _seed, ask

        await _seed(client)
        await login(client, "sales")
        answer = await ask(client, "What does error code E-04 mean?")

        assert answer["done"]["citations"] == []
        assert (await db.execute(select(CachedAnswer))).scalars().all() == []

    async def test_the_last_updated_timestamp_still_moves_on_a_hit(
        self, client: AsyncClient
    ) -> None:
        """A cached turn is still a turn: it belongs in the conversation and in
        the history sidebar, or the reader loses the thread they just had."""
        from tests.test_chat import _seed, ask

        await _seed(client)
        await login(client, "technician")
        first = await ask(client, "What does error code E-04 mean?")
        await ask(client, "What does error code E-04 mean?", first["done"]["conversationId"])

        conversation_id = first["done"]["conversationId"]
        detail = (await client.get(f"/chat/conversations/{conversation_id}")).json()
        assert len(detail["messages"]) == 4


class TestTheExactTermGuard:
    """The finding that changed the design, from running against real
    embeddings.

    `text-embedding-3-small` scores "what does E-04 mean" against "what does
    E-14 mean" at 0.82 — higher than two genuine rewordings of the same
    question. The two distributions overlap, so no similarity threshold both
    admits rewordings and excludes different questions.

    Which is AD-2 arriving somewhere new: embeddings collapse exactly the
    surface differences a part number consists of. The retriever answers that
    with a keyword leg; the cache answers it by putting the extracted codes in
    the key.
    """

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("What does error code E-04 mean?", "E-04"),
            ("what is E-14", "E-14"),
            ("How long does a regeneration take on the NG-4200?", "NG-4200"),
            ("Is the NG-4200 or the NG-6800 quicker?", "NG-4200,NG-6800"),
            ("What is our warranty policy?", ""),
        ],
    )
    def test_the_key_is_the_codes_in_the_question(self, question: str, expected: str) -> None:
        assert cache.term_key(question) == expected

    async def test_a_different_fault_code_cannot_hit(self, db: AsyncSession) -> None:
        """The case the guard exists for. These two vectors are *identical*
        here, which is stronger than the 0.82 they score in reality — and it
        still misses, because the codes differ."""
        principal = _principal(Role.TECHNICIAN)
        await cache.store(
            db,
            question="What does error code E-04 mean?",
            embedding=_vector(1.0),
            answer="A stuck brine valve.",
            citations=[{"marker": "S1"}],
            principal=principal,
        )
        await db.commit()

        hit = await cache.lookup(
            db,
            question="What does error code E-14 mean?",
            embedding=_vector(1.0),
            principal=principal,
            threshold=0.5,
            ttl=TTL,
        )

        assert hit is None

    async def test_a_different_model_number_cannot_hit(self, db: AsyncSession) -> None:
        """0.905 in reality — the highest of every pair that must not match, and
        the two softener manuals are near-copies of one another, so the wrong
        answer would be plausible down to its section name."""
        principal = _principal(Role.TECHNICIAN)
        await cache.store(
            db,
            question="How long does a regeneration take on the NG-4200?",
            embedding=_vector(1.0),
            answer="96 minutes.",
            citations=[{"marker": "S1"}],
            principal=principal,
        )
        await db.commit()

        hit = await cache.lookup(
            db,
            question="How long does a regeneration take on the NG-6800?",
            embedding=_vector(1.0),
            principal=principal,
            threshold=0.5,
            ttl=TTL,
        )

        assert hit is None

    async def test_the_same_code_reworded_still_hits(self, db: AsyncSession) -> None:
        """The guard narrows what may match; it does not replace the vector."""
        principal = _principal(Role.TECHNICIAN)
        await cache.store(
            db,
            question="What does error code E-04 mean?",
            embedding=_vector(1.0),
            answer="A stuck brine valve.",
            citations=[{"marker": "S1"}],
            principal=principal,
        )
        await db.commit()

        hit = await cache.lookup(
            db,
            question="what is error code E-04",
            embedding=_vector(0.99, 0.14),
            principal=principal,
            threshold=0.92,
            ttl=TTL,
        )

        assert hit is not None
        assert hit.answer == "A stuck brine valve."

    async def test_two_questions_with_no_codes_still_match_on_the_vector(
        self, db: AsyncSession
    ) -> None:
        """The guard is a narrowing, not a requirement. Most questions contain
        no code at all, and those fall back to similarity alone — which is why
        the threshold is still 0.92 rather than something relaxed."""
        principal = _principal(Role.OFFICE)
        await cache.store(
            db,
            question="What is our warranty policy?",
            embedding=_vector(1.0),
            answer="Twelve months.",
            citations=[{"marker": "S1"}],
            principal=principal,
        )
        await db.commit()

        hit = await cache.lookup(
            db,
            question="How long is our warranty?",
            embedding=_vector(0.99, 0.14),
            principal=principal,
            threshold=0.92,
            ttl=TTL,
        )

        assert hit is not None
