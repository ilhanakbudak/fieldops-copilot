"""The chat endpoint.

Streamed, so the assertions are about the event sequence rather than a response
body. The three that matter: sources arrive before text, citations resolve to
real documents, and one employee cannot read another's conversation.
"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.rag.corpus import seed_corpus
from tests.conftest import login


async def _seed(client: AsyncClient) -> None:
    """Ingest the fixture corpus through the application's own session."""
    from app.db.engine import session_scope

    async with session_scope() as db:
        await seed_corpus(db, settings=get_settings())


async def ask(client: AsyncClient, question: str, conversation_id: str | None = None) -> dict:
    """Drive the stream and collect it into something assertable."""
    events: list[tuple[str, dict]] = []
    body: dict = {"question": question}
    if conversation_id:
        body["conversationId"] = conversation_id

    async with client.stream("POST", "/chat", json=body) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")

        name = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))

    text = "".join(payload["text"] for kind, payload in events if kind == "delta")
    return {
        "order": [kind for kind, _ in events],
        "sources": next((p for k, p in events if k == "sources"), None),
        "done": next((p for k, p in events if k == "done"), None),
        "error": next((p for k, p in events if k == "error"), None),
        "text": text,
    }


async def test_sources_arrive_before_any_text(client: AsyncClient) -> None:
    """So a reader watching an answer form can see what it is being drawn from —
    and, when the answer is wrong, whether retrieval or generation was at
    fault."""
    await _seed(client)
    await login(client, "technician")

    result = await ask(client, "What does error code E-04 mean?")

    assert result["error"] is None
    assert result["order"][0] == "sources"
    assert result["order"][-1] == "done"
    assert result["order"].index("sources") < result["order"].index("delta")


async def test_an_answer_cites_documents_that_were_actually_retrieved(
    client: AsyncClient,
) -> None:
    await _seed(client)
    await login(client, "technician")

    result = await ask(client, "What does error code E-04 mean?")

    retrieved = {source["documentTitle"] for source in result["sources"]["sources"]}
    citations = result["done"]["citations"]

    assert citations
    assert all(citation["documentTitle"] in retrieved for citation in citations)
    assert all(citation["chunkId"] for citation in citations)


async def test_the_stream_reports_what_the_answer_cost(client: AsyncClient) -> None:
    """Zero for the mock provider, but the accounting path has to be exercised
    or it stays broken until the day a real key is configured."""
    await _seed(client)
    await login(client, "technician")

    usage = (await ask(client, "What does error code E-04 mean?"))["done"]["usage"]

    assert usage["inputTokens"] > 0
    assert usage["outputTokens"] > 0
    assert usage["estimatedCostUsd"] >= 0


async def test_a_question_with_no_readable_source_is_declined(client: AsyncClient) -> None:
    """Not answered from general knowledge. A plausible answer about somebody
    else's equipment is worse than no answer."""
    await _seed(client)
    await login(client, "sales")

    result = await ask(client, "What does error code E-04 mean?")

    assert result["done"]["citations"] == []
    assert "could not find" in result["text"].lower()


async def test_pricing_is_never_among_the_sources_for_a_technician(
    client: AsyncClient,
) -> None:
    await _seed(client)
    await login(client, "technician")

    result = await ask(client, "What is the dealer cost of a radon aeration system?")

    titles = {source["documentTitle"] for source in result["sources"]["sources"]}
    assert "Dealer Price List and Margin Guide" not in titles


async def test_the_conversation_is_persisted_with_its_citations(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A cited answer is evidence of what the assistant told an employee. It
    does not live only in a browser tab."""
    await _seed(client)
    await login(client, "technician")

    result = await ask(client, "What does error code E-04 mean?")
    conversation_id = result["done"]["conversationId"]

    detail = await client.get(f"/chat/conversations/{conversation_id}")

    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["citations"]


async def test_a_follow_up_continues_the_same_conversation(client: AsyncClient) -> None:
    await _seed(client)
    await login(client, "technician")

    first = await ask(client, "What does error code E-04 mean?")
    conversation_id = first["done"]["conversationId"]

    second = await ask(client, "And what about E-14?", conversation_id)

    assert second["done"]["conversationId"] == conversation_id

    detail = (await client.get(f"/chat/conversations/{conversation_id}")).json()
    assert len(detail["messages"]) == 4


async def test_the_title_comes_from_the_first_question(client: AsyncClient) -> None:
    await _seed(client)
    await login(client, "technician")

    await ask(client, "What does error code E-04 mean?")

    conversations = (await client.get("/chat/conversations")).json()
    assert conversations[0]["title"] == "What does error code E-04 mean?"


async def test_one_employee_cannot_read_another_s_conversation(
    client: AsyncClient, second_client: object
) -> None:
    """Scoped to the owner, not to the role. What somebody asked the assistant
    is more revealing than what they were allowed to read."""
    await _seed(client)
    await login(client, "technician")
    conversation_id = (await ask(client, "What does error code E-04 mean?"))["done"][
        "conversationId"
    ]

    await login(client, "admin")

    assert (await client.get(f"/chat/conversations/{conversation_id}")).status_code == 404
    assert (await client.delete(f"/chat/conversations/{conversation_id}")).status_code == 404


async def test_asking_into_someone_else_s_conversation_is_refused(
    client: AsyncClient,
) -> None:
    await _seed(client)
    await login(client, "technician")
    conversation_id = (await ask(client, "What does error code E-04 mean?"))["done"][
        "conversationId"
    ]

    await login(client, "office")
    result = await ask(client, "What was that again?", conversation_id)

    assert result["error"] is not None
    assert result["done"] is None


async def test_chat_requires_a_session(client: AsyncClient) -> None:
    response = await client.post("/chat", json={"question": "anything"})

    assert response.status_code == 401


async def test_an_answer_is_recorded_in_the_audit_trail(
    client: AsyncClient, db: AsyncSession
) -> None:
    from sqlalchemy import select

    from app.db.models import AuditEvent

    await _seed(client)
    await login(client, "technician")
    await ask(client, "What does error code E-04 mean?")

    events = list(
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "ai.answer"))).scalars()
    )

    assert len(events) == 1
    detail = json.loads(events[0].detail or "{}")
    assert detail["passages"] >= 1
    assert "costUsd" in detail
