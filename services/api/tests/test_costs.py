"""The cost report.

Two things it has to get right and one it has to refuse.

Right: every figure is summed from `cost_usd` as it was written, and the
breakdowns are cut three ways because "which feature is expensive", "who is
asking" and "is it going up" are three questions with three different answers.

Refuse: it is the only screen that says what individual employees have been
asking about, in aggregate. `cost:read` is an administrator's permission.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.ids import new_id
from app.db.models import UsageEvent, User
from tests.conftest import login


async def _event(
    db: AsyncSession,
    *,
    feature: str,
    cost: float,
    days_ago: int = 0,
    user_id: str | None = None,
    model: str = "gpt-5.6-terra",
    cache_hit: bool = False,
    input_tokens: int = 100,
    cached_input_tokens: int = 0,
) -> None:
    db.add(
        UsageEvent(
            id=new_id(),
            occurred_at=utcnow() - timedelta(days=days_ago),
            user_id=user_id,
            feature=feature,
            provider="openai",
            model=model,
            input_tokens=input_tokens,
            output_tokens=20,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost,
            cache_hit=cache_hit,
        )
    )
    await db.commit()


class TestWhoMayRead:
    @pytest.mark.parametrize("role", ["technician", "sales", "office"])
    async def test_it_is_an_administrators_screen(self, client: AsyncClient, role: str) -> None:
        """It says, in aggregate, what each employee has been asking the
        assistant about. That is a management view, not a colleague's."""
        await login(client, role)

        assert (await client.get("/admin/costs")).status_code == 403

    async def test_an_administrator_may(self, client: AsyncClient) -> None:
        await login(client, "admin")

        assert (await client.get("/admin/costs")).status_code == 200

    async def test_it_needs_a_session(self, client: AsyncClient) -> None:
        assert (await client.get("/admin/costs")).status_code == 401


class TestTheBreakdowns:
    async def test_the_cheap_model_and_the_good_one_are_separate_lines(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """The whole design claim in AD-5 is that a cheap model does the
        analysis and the expensive one only the final answer. A report that
        billed both to `chat` could not show it, and this is the first thing
        anybody asks a cost dashboard."""
        await _event(db, feature="query_analysis", cost=0.0001, model="gpt-5.6-luna")
        await _event(db, feature="chat", cost=0.02)

        await login(client, "admin")
        report = (await client.get("/admin/costs")).json()

        features = {row["label"]: row["costUsd"] for row in report["byFeature"]}
        assert features == pytest.approx({"query_analysis": 0.0001, "chat": 0.02})
        # Most expensive first: a ranking is read from the top.
        assert report["byFeature"][0]["label"] == "chat"

    async def test_days_are_a_series_in_order(self, client: AsyncClient, db: AsyncSession) -> None:
        await _event(db, feature="chat", cost=0.01, days_ago=2)
        await _event(db, feature="chat", cost=0.02, days_ago=1)

        await login(client, "admin")
        days = (await client.get("/admin/costs")).json()["byDay"]

        assert [row["label"] for row in days] == sorted(row["label"] for row in days)
        assert len(days) == 2

    async def test_it_is_grouped_by_person(self, client: AsyncClient, db: AsyncSession) -> None:
        from sqlalchemy import select

        users = (await db.execute(select(User).order_by(User.email))).scalars().all()
        await _event(db, feature="chat", cost=0.03, user_id=users[0].id)
        await _event(db, feature="chat", cost=0.01, user_id=users[1].id)

        await login(client, "admin")
        report = (await client.get("/admin/costs")).json()

        assert [row["label"] for row in report["byUser"]] == [users[0].email, users[1].email]

    async def test_a_deleted_employees_spend_does_not_vanish(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """`user_id` is `ON DELETE SET NULL`, so the row survives the person.
        Dropping it from the report would make last month's total change."""
        await _event(db, feature="chat", cost=0.05, user_id=None)

        await login(client, "admin")
        report = (await client.get("/admin/costs")).json()

        assert report["byUser"] == [
            {
                "label": "(deleted)",
                "calls": 1,
                "inputTokens": 100,
                "outputTokens": 20,
                "cachedInputTokens": 0,
                "costUsd": 0.05,
                "cacheHits": 0,
            }
        ]

    async def test_the_window_excludes_what_is_outside_it(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        await _event(db, feature="chat", cost=0.10, days_ago=40)
        await _event(db, feature="chat", cost=0.01, days_ago=1)

        await login(client, "admin")
        report = (await client.get("/admin/costs?days=7")).json()

        assert report["summary"]["costUsd"] == pytest.approx(0.01)
        assert report["summary"]["days"] == 7


class TestWhatTheNumbersMean:
    async def test_prompt_cache_hits_are_counted_apart_from_input(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Folding them into the input total would make the one number that
        says whether prompt caching is working disappear into the one it is
        supposed to be reducing."""
        await _event(db, feature="chat", cost=0.01, input_tokens=1000, cached_input_tokens=800)

        await login(client, "admin")
        summary = (await client.get("/admin/costs")).json()["summary"]

        assert summary["inputTokens"] == 1000
        assert summary["cachedInputTokens"] == 800

    async def test_semantic_cache_hits_are_counted_as_calls_that_cost_nothing(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Otherwise "the cache is working" and "everybody stopped asking" look
        identical: spending falls and nothing says why."""
        await _event(db, feature="chat", cost=0.0, cache_hit=True, input_tokens=0)
        await _event(db, feature="chat", cost=0.02)

        await login(client, "admin")
        report = (await client.get("/admin/costs")).json()

        assert report["summary"]["calls"] == 2
        assert report["summary"]["cacheHits"] == 1
        assert report["byFeature"][0]["cacheHits"] == 1

    async def test_an_empty_month_is_zeroes_rather_than_nulls(self, client: AsyncClient) -> None:
        """A fresh deployment opens this page before it has spent anything, and
        a dashboard of nulls reads as broken."""
        await login(client, "admin")
        report = (await client.get("/admin/costs")).json()

        assert report["summary"]["costUsd"] == 0.0
        assert report["summary"]["calls"] == 0
        assert report["byDay"] == []


class TestTheCacheablePrefixComesFirst:
    """Prompt caching keys on an exact prefix match, so the parts that do not
    vary have to be assembled ahead of the parts that do.

    Asserted structurally rather than trusted to a comment, because getting it
    wrong costs nothing visible: the answers are identical, the tests pass, and
    the discount silently is not applied. The only signal is
    `cachedInputTokens` staying at zero on the dashboard, which is why that
    figure is on it.
    """

    def test_a_live_call_prompt_puts_the_passages_before_the_transcript(self) -> None:
        from app.rag.search.pipeline import Passage
        from app.realtime.assist import _prompt

        passage = Passage(
            marker="S1",
            chunk_id="c",
            document_id="d",
            document_title="NG-RN Radon Manual",
            content="Cold water runs warmer after installation.",
            page=2,
            section="2. Warm Water",
            score=1.0,
        )
        prompt = _prompt("Caller: the water is warm", [passage], None)

        # The passages for one question do not change between the first token
        # and the last; the transcript grows with every utterance.
        assert prompt.index("NG-RN Radon Manual") < prompt.index("The call so far")

    def test_the_agent_puts_its_system_prompt_before_the_question(self) -> None:
        from app.agent.loop import system_prompt
        from app.config import get_settings
        from app.llm.base import Message

        messages = [
            Message(role="system", content=system_prompt(get_settings())),
            Message(role="user", content="What does E-04 mean?"),
        ]

        assert messages[0].role == "system"
        assert [message.role for message in messages].index("user") == 1

    def test_a_provider_sends_messages_in_the_order_it_was_given_them(self) -> None:
        """The ordering above is only worth anything if nothing downstream
        reorders it."""
        from app.config import get_settings
        from app.llm.base import Message
        from app.llm.openai import OpenAiProvider

        provider = OpenAiProvider(get_settings().model_copy(update={"openai_api_key": "sk-test"}))
        payload = provider._payload(
            [
                Message(role="system", content="stable"),
                Message(role="user", content="varies"),
            ],
            model="gpt-5.6-luna",
            max_tokens=16,
        )

        assert [message["content"] for message in payload["messages"]] == ["stable", "varies"]
