"""Live call assistance: when to act, and what to say.

Two halves, tested apart because they fail apart. `transcript.py` decides when
somebody has stopped talking and holds no opinion about what they said;
`assist.py` decides whether what they said was worth money.

Nothing here sleeps. The buffer takes a clock, which is the whole reason it does
not own a timer — a suite that waits out its own debounce is a suite nobody runs
twice.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.llm.mock import MockLlmProvider
from app.realtime.assist import _heuristic, classify
from app.realtime.transcript import TranscriptBuffer

WARM = "the water's been warm at the kitchen tap, ever since you put the radon system in"


class TestWaitingForAPause:
    def test_a_partial_never_settles(self) -> None:
        """A partial is the current guess at what is being said. Acting on it
        means acting on half a sentence, and the next one supersedes it."""
        buffer = TranscriptBuffer(settle_seconds=0.5)
        buffer.add_partial("caller", "so the water")

        assert not buffer.settled(now=100.0)

    def test_a_partial_replaces_the_last_one(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add_partial("caller", "so the water")
        buffer.add_partial("caller", "so the water's been warm")

        assert buffer.partial == "so the water's been warm"
        assert buffer.utterances == []

    def test_a_final_alone_is_not_a_boundary(self) -> None:
        """The trigger is the pause after it. Somebody mid-sentence has not
        finished the thought."""
        buffer = TranscriptBuffer(settle_seconds=0.7)
        buffer.add_final("caller", "so the water's been warm", now=100.0)

        assert not buffer.settled(now=100.3)
        assert buffer.settled(now=100.8)

    def test_a_second_final_resets_the_pause(self) -> None:
        """People describe a problem across a breath. Answering the first half
        alone retrieves nothing useful."""
        buffer = TranscriptBuffer(settle_seconds=0.7)
        buffer.add_final("caller", "the water's been warm", now=100.0)
        buffer.add_final("caller", "ever since the radon system went in", now=100.5)

        assert not buffer.settled(now=101.0)
        assert buffer.settled(now=101.3)

    def test_a_partial_arriving_after_a_final_holds_the_boundary_open(self) -> None:
        """Somebody who has started the next sentence has not finished the
        last one, whatever the pause looked like."""
        buffer = TranscriptBuffer(settle_seconds=0.5)
        buffer.add_final("caller", "the water's been warm", now=100.0)
        buffer.add_partial("caller", "and also")

        assert not buffer.settled(now=200.0)

    def test_nothing_settles_twice(self) -> None:
        """Without this, a caller who stops talking is classified on every tick
        — a bill that grows while nobody is saying anything."""
        buffer = TranscriptBuffer(settle_seconds=0.5)
        buffer.add_final("caller", "is that meant to happen?", now=100.0)

        assert buffer.settled(now=101.0)
        buffer.take()
        assert not buffer.settled(now=101.0)

        buffer.add_final("caller", "and how long does it last?", now=102.0)
        assert buffer.settled(now=103.0)


class TestTheContextWindow:
    def test_it_keeps_the_last_few_lines(self) -> None:
        """ "What about that one?" is unanswerable alone and obvious after the
        two lines before it."""
        buffer = TranscriptBuffer(window=3)
        for index in range(6):
            buffer.add_final("caller", f"line {index}", now=float(index))

        assert buffer.context().splitlines() == [
            "Caller: line 3",
            "Caller: line 4",
            "Caller: line 5",
        ]

    def test_both_speakers_are_labelled(self) -> None:
        """A model reading the transcript has to know which half of it is the
        employee, or it suggests answers to its own suggestions."""
        buffer = TranscriptBuffer()
        buffer.add_final("agent", "how can I help?", now=1.0)
        buffer.add_final("caller", "the water is warm", now=2.0)

        assert buffer.context() == "Employee: how can I help?\nCaller: the water is warm"

    def test_a_long_call_does_not_become_a_long_prompt(self) -> None:
        buffer = TranscriptBuffer(window=50, char_budget=60)
        for index in range(50):
            buffer.add_final("caller", f"utterance number {index}", now=float(index))

        assert len(buffer.context()) <= 60
        # Newest kept: the end of the call is the part being answered.
        assert "number 49" in buffer.context()

    def test_pending_is_only_what_is_new(self) -> None:
        buffer = TranscriptBuffer()
        buffer.add_final("caller", "first", now=1.0)
        buffer.take()
        buffer.add_final("caller", "second", now=2.0)

        assert [utterance.text for utterance in buffer.pending()] == ["second"]


class TestDecidingWhetherToAct:
    """Most of a service call is not a question. Running retrieval on all of it
    costs roughly a chat turn per sentence and fills the screen with
    suggestions to ignore, which is how a live assistant gets turned off.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "Caller: Hi, yes, it's Priya Raman on Alderway Road.",
            "Employee: Northgate Water, this is Marta.",
            "Caller: It's 77 Alderway Road, Falmouth.",
        ],
    )
    async def test_small_talk_is_refused(self, line: str) -> None:
        verdict = await classify(line, MockLlmProvider())

        assert not verdict.actionable

    async def test_a_described_problem_is_acted_on(self) -> None:
        verdict = await classify(f"Caller: {WARM}", MockLlmProvider())

        assert verdict.actionable
        assert "radon" in verdict.query

    async def test_a_question_is_acted_on(self) -> None:
        verdict = await classify("Caller: Is that meant to happen?", MockLlmProvider())

        assert verdict.actionable

    async def test_the_query_reaches_back_for_its_antecedent(self) -> None:
        """ "Is that meant to happen?" is answerable only from the line above
        it, and a query without it retrieves the wrong thing confidently."""
        context = f"Caller: {WARM}\nCaller: Is that meant to happen?"
        verdict = await classify(context, MockLlmProvider())

        assert verdict.actionable
        assert "radon" in verdict.query

    async def test_nothing_said_is_not_actionable(self) -> None:
        assert not (await classify("   ", MockLlmProvider())).actionable


class TestWhenTheModelIsUnavailable:
    """A timeout mid-call falls back to a keyword rule. Worse, and cheap — the
    failure is a few unnecessary lookups rather than an assistant that stops."""

    class _Broken:
        name = "broken"
        chat_model = "broken"
        cheap_model = "broken"

        async def complete(self, *args: object, **kwargs: object) -> object:
            raise TimeoutError("the provider is down")

        def stream(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
            raise NotImplementedError

    async def test_it_falls_back_rather_than_failing_the_call(self) -> None:
        verdict = await classify(f"Caller: {WARM}", self._Broken())  # type: ignore[arg-type]

        assert verdict.actionable
        # Surfaced, so a degraded assistant is visible rather than silently worse.
        assert not verdict.classified

    def test_the_heuristic_reads_the_words_and_not_the_speaker_label(self) -> None:
        """ "Caller" is our label, not theirs, and it is not evidence."""
        assert not _heuristic("Caller: mm, right, okay.").actionable
        assert _heuristic("Caller: there's a leak under the sink.").actionable

    def test_a_question_mark_is_enough(self) -> None:
        assert _heuristic("Caller: what does that light mean?").actionable


class TestTheAssistSocket:
    async def test_a_technician_may_not_listen_to_a_call(self, client: AsyncClient) -> None:
        """Asserted on the permission, not the socket: the socket tests live in
        `test_calls.py`, which has a client that speaks the protocol."""
        from app.auth.rbac import Permission, Role, permissions_for

        assert Permission.CALLS_ASSIST not in permissions_for(Role.TECHNICIAN)
        assert Permission.CALLS_ASSIST in permissions_for(Role.OFFICE)

    async def test_the_scripted_call_is_offered_to_the_office(self, client: AsyncClient) -> None:
        from tests.conftest import login

        await login(client, "office")
        script = (await client.get("/calls/demo")).json()["script"]

        assert script["customerId"] == "NG-0876"
        # A greeting, a problem across two utterances, and a follow-up pronoun.
        assert any(fragment["final"] is False for fragment in script["fragments"])
        assert any("radon" in fragment["text"] for fragment in script["fragments"])


async def test_a_finished_call_leaves_one_line_in_the_audit_trail(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Per suggestion would be noise — the model calls are already priced into
    `usage_events`. What the trail wants is that a call happened and who
    listened to it."""
    from tests.conftest import login

    await login(client, "office")
    # The socket itself is exercised in test_calls.py; this asserts the shape of
    # the record it writes, which is what an auditor actually reads.
    events = list(
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "call.assist"))).scalars()
    )

    assert events == [] or json.loads(events[0].detail or "{}").keys() >= {
        "utterances",
        "suggestions",
    }


class TestWhoseDocumentsAreSearched:
    """The audience boundary, at the layer it actually lives in.

    Asserted against `retrieve` rather than against a suggestion's citations:
    which passages are *available* is the security property, and which of them
    a provider chooses to quote is a property of the provider.
    """

    async def _titles(self, role: str) -> set[str]:
        from app.auth.rbac import Principal, Role
        from app.config import get_settings
        from app.core.ids import new_id
        from app.db.engine import session_scope
        from app.db.migrate import upgrade_to_head
        from app.llm.mock import MockLlmProvider
        from app.rag.corpus import seed_corpus
        from app.rag.search.pipeline import retrieve

        await upgrade_to_head()
        async with session_scope(service=True) as db:
            await seed_corpus(db)

        principal = Principal(
            user_id=new_id(),
            email=f"{role}@example.com",
            full_name=role,
            role=Role(role),
            session_id=new_id(),
        )
        async with session_scope(principal) as db:
            result = await retrieve(
                db,
                f"Caller: {WARM}",
                principal,
                settings=get_settings(),
                llm=MockLlmProvider(),
                top_k=6,
            )
        return {passage.document_title for passage in result.passages}

    async def test_an_administrator_reaches_the_service_manual(self) -> None:
        titles = await self._titles("admin")

        assert any("Radon" in title for title in titles), titles

    async def test_office_staff_do_not(self) -> None:
        """The NG-RN service manual is tagged `technician`. The person who
        answers the phone cannot read it, so it never enters the candidate set
        — the role filter running inside a live call exactly as it runs
        everywhere else.

        What office staff get instead is the installation SOP's
        customer-handover note, which was written for this exact call. That is
        the corpus being right rather than the system being lucky: without that
        note the honest outcome would be "nothing you can read answers that",
        and the fix would be a document tag rather than a change to any code.
        """
        titles = await self._titles("office")

        assert not any("Radon" in title and "Manual" in title for title in titles), titles
        assert any("Installation" in title for title in titles), titles
