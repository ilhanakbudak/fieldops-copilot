"""The chat endpoint.

Server-sent events rather than a WebSocket. The traffic is one-directional — the
question goes up in the POST, the answer comes down — and SSE is a plain HTTP
response, so it inherits the session cookie, the proxy configuration and the
audit middleware without any of them being special-cased. A WebSocket would need
its own authentication path, which is one more place to get authorisation wrong.
The live-call feature genuinely is bidirectional and will use one; this is not.

The event sequence, now that a turn can involve tools:

    tool       a tool is about to run — the model decided to use it
    tool_done  what it returned, with its structured payload
    sources    retrieved passages, when the knowledge tool was one of them
    delta      answer text, as it arrives
    done       resolved citations, token usage and cost
    error      something failed mid-stream

The tool events are not decoration. A reader watching "Searching the knowledge
base" appear, then "Found 2 customers", can see *what the assistant decided to
do* — and when the answer is wrong, whether the decision or the execution was at
fault.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, cast

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import ToolContext, run_agent, tools_for
from app.api.deps import DbDep, PrincipalDep, SettingsDep, require
from app.api.schemas import (
    AskRequest,
    ChatMessageOut,
    CitationOut,
    ConversationDetail,
    ConversationSummary,
)
from app.audit import audit, record_usage
from app.auth.rbac import Permission, Principal
from app.config import Settings
from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.db.engine import session_scope
from app.db.models import ChatMessage, Conversation
from app.llm import Message, cache, cost_usd, get_llm_provider
from app.llm.cache import CacheHit
from app.rag.cite import resolve
from app.rag.embed import get_embedding_provider
from app.rag.search.pipeline import Passage

logger = logging.getLogger("fieldops.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

# How much of a conversation is replayed to the model. Enough to resolve "what
# about the other one?", short enough that a long thread does not quietly become
# the most expensive part of every request.
HISTORY_TURNS = 8


def _sse(event: str, payload: object) -> str:
    # `ensure_ascii=False` keeps the em dashes and degree signs in a manual
    # intact instead of shipping escape sequences to the browser.
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@router.post("", dependencies=[require(Permission.DOCUMENTS_READ)])
async def ask(
    body: AskRequest,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Ask a question. The response is an event stream.

    This handler deliberately does **not** take the request-scoped database
    session. That session is committed and closed by its dependency as soon as
    the handler returns — which, for a streaming response, is before a single
    token has been generated. The generator opens its own scopes instead.
    """
    return StreamingResponse(
        _generate(body, principal, settings),
        media_type="text/event-stream",
        headers={
            # Without this an nginx or a CDN in front of the service will buffer
            # the whole stream and deliver it as one lump, which looks exactly
            # like the model being slow.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


async def _generate(
    body: AskRequest, principal: Principal, settings: Settings
) -> AsyncIterator[str]:
    question = body.question.strip()
    llm = get_llm_provider()
    passages: list[Passage] = []

    try:
        async with session_scope(principal) as db:
            conversation = await _conversation(db, principal, body.conversation_id, question)
            conversation_id = conversation.id

            if body.edit_message_id:
                await _truncate_from(db, conversation_id, body.edit_message_id)

            history = await _history(db, conversation_id)
            db.add(
                ChatMessage(
                    id=new_id(),
                    conversation_id=conversation_id,
                    role="user",
                    content=question,
                    created_at=utcnow(),
                )
            )

        yield _sse("start", {"conversationId": conversation_id})

        # Asked before the agent runs, and only for a first turn — a follow-up
        # means something different in every conversation, so its answer is not
        # a function of its own words. See app/llm/cache.py.
        cached = await _cache_lookup(question, principal, settings) if not history else None
        if cached is not None:
            async for frame in _replay(cached, conversation_id, question, principal):
                yield frame
            return

        # One session for the whole turn: tools query the database, and opening
        # a scope per tool call would mean a new transaction — and on Postgres a
        # new `SET LOCAL` — for every step.
        async with session_scope(principal) as db:
            tools = tools_for(principal)
            context = ToolContext(principal=principal, session=db)

            text_parts: list[str] = []
            usage = None
            tool_runs: list[dict[str, Any]] = []

            async for event in run_agent(
                question, tools, llm, context, settings=settings, history=history
            ):
                if event.tool_started is not None:
                    yield _sse(
                        "tool",
                        {"id": event.tool_started.id, "name": event.tool_started.name},
                    )

                if event.tool_finished is not None:
                    run = event.tool_finished
                    tool_runs.append(
                        {
                            "name": run.name,
                            "summary": run.result.summary,
                            "ok": run.result.ok,
                            "durationMs": run.duration_ms,
                        }
                    )
                    yield _sse(
                        "tool_done",
                        {
                            "id": run.id,
                            "name": run.name,
                            "summary": run.result.summary,
                            "ok": run.result.ok,
                            "durationMs": run.duration_ms,
                            # Structured payload for the interface — customer
                            # cards, source lists. The model never sees this.
                            "data": _public(run.result.data),
                        },
                    )

                    found = run.result.data.get("passages")
                    if found:
                        passages.extend(cast("list[Passage]", found))
                        yield _sse("sources", {"sources": [_source(p) for p in found]})

                if event.delta:
                    text_parts.append(event.delta)
                    yield _sse("delta", {"text": event.delta})

                if event.done:
                    usage = event.usage

        answer = resolve("".join(text_parts), passages)
        if answer.dropped:
            logger.warning(
                "model cited %d marker(s) that were never retrieved: %s",
                len(answer.dropped),
                ", ".join(sorted(set(answer.dropped))),
            )

        total = usage or _empty_usage()
        cost = cost_usd(llm.chat_model, total)

        async with session_scope(principal) as db:
            message_id = new_id()
            db.add(
                ChatMessage(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer.text,
                    citations=json.dumps([_citation(c) for c in answer.citations]),
                    input_tokens=total.input_tokens,
                    output_tokens=total.output_tokens,
                    created_at=utcnow(),
                )
            )
            await _touch(db, conversation_id)

        await record_usage(
            feature="chat",
            provider=llm.name,
            model=llm.chat_model,
            input_tokens=total.input_tokens,
            output_tokens=total.output_tokens,
            cached_input_tokens=total.cached_input_tokens,
            cost_usd=cost,
            principal=principal,
        )

        await _cache_store(
            question,
            answer.text,
            [_citation(c) for c in answer.citations],
            tools_used=[run["name"] for run in tool_runs],
            has_history=bool(history),
            principal=principal,
            settings=settings,
        )
        await audit(
            "ai.answer",
            principal=principal,
            resource_type="conversation",
            resource_id=conversation_id,
            detail={
                "question": question[:200],
                "tools": [run["name"] for run in tool_runs],
                "passages": len(passages),
                "citations": len(answer.citations),
                "droppedMarkers": len(answer.dropped),
                "costUsd": round(cost, 6),
            },
        )

        yield _sse(
            "done",
            {
                "conversationId": conversation_id,
                "messageId": message_id,
                "text": answer.text,
                "citations": [_citation(c) for c in answer.citations],
                "tools": tool_runs,
                "usage": {
                    "inputTokens": total.input_tokens,
                    "outputTokens": total.output_tokens,
                    "cachedInputTokens": total.cached_input_tokens,
                    "estimatedCostUsd": cost,
                },
            },
        )
    except Exception:
        # The response status was sent long ago, so an exception here cannot
        # become a 500. Telling the client on the stream is the only way it
        # finds out at all.
        logger.exception("chat stream failed")
        yield _sse("error", {"message": "Something went wrong generating this answer."})


def _empty_usage() -> Any:
    from app.llm.base import Usage

    return Usage()


# --- The semantic cache -----------------------------------------------------


async def _cache_lookup(question: str, principal: Principal, settings: Settings) -> CacheHit | None:
    """A stored answer for a question close enough to this one.

    Never fails the turn. A cache that cannot be read is a system that is merely
    slower, and the embedding call it needs is the one part of this that can be
    slow or unavailable.
    """
    if not settings.semantic_cache_enabled:
        return None

    try:
        embedder = get_embedding_provider()
        vector = await asyncio.to_thread(embedder.embed_query, question)
        async with session_scope(principal) as db:
            return await cache.lookup(
                db,
                question=question,
                embedding=vector,
                principal=principal,
                threshold=settings.semantic_cache_threshold,
                ttl=timedelta(hours=settings.semantic_cache_ttl_hours),
            )
    except Exception:
        logger.warning("semantic cache lookup failed; answering normally", exc_info=True)
        return None


async def _cache_store(
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
    *,
    tools_used: list[str],
    has_history: bool,
    principal: Principal,
    settings: Settings,
) -> None:
    """Keep this answer, if keeping it would be correct.

    `cacheable` is the rule and it lives in one place. What is decided here is
    only whether to bother asking — an answer with no citations came from a
    search that found nothing, and storing "I could not find anything" would
    make the cache good at repeating a failure.
    """
    if not settings.semantic_cache_enabled or not citations:
        return
    if not cache.cacheable(tools_used=tools_used, has_history=has_history):
        return

    try:
        embedder = get_embedding_provider()
        vector = await asyncio.to_thread(embedder.embed_query, question)
        async with session_scope(principal) as db:
            await cache.store(
                db,
                question=question,
                embedding=vector,
                answer=answer,
                citations=citations,
                principal=principal,
            )
            # Swept on the way past rather than on a schedule: a background
            # sweeper is a second thing to run and monitor for a table that is
            # small by construction.
            await cache.purge(db, ttl=timedelta(hours=settings.semantic_cache_ttl_hours))
    except Exception:
        logger.warning("could not store an answer in the semantic cache", exc_info=True)


async def _replay(
    hit: CacheHit, conversation_id: str, question: str, principal: Principal
) -> AsyncIterator[str]:
    """Serve a stored answer down the same stream a fresh one uses.

    The whole text in one `delta` rather than word by word. A cache hit that
    pretended to type would be a system spending latency to look busy, and the
    honest thing on a hit is that the answer is simply there.
    """
    citations = [
        CitationOut.model_validate(item) if not isinstance(item, dict) else item
        for item in hit.citations
    ]
    yield _sse("delta", {"text": hit.answer})

    async with session_scope(principal) as db:
        message_id = new_id()
        db.add(
            ChatMessage(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=hit.answer,
                citations=json.dumps(hit.citations),
                created_at=utcnow(),
            )
        )
        await _touch(db, conversation_id)

    # Recorded with no tokens and no cost, but recorded. Without this the
    # dashboard shows spending fall and cannot say whether the cache is working
    # or everybody stopped asking.
    llm = get_llm_provider()
    await record_usage(
        feature="chat",
        provider=llm.name,
        model=llm.chat_model,
        cost_usd=0.0,
        cache_hit=True,
        principal=principal,
    )
    await audit(
        "ai.answer",
        principal=principal,
        resource_type="conversation",
        resource_id=conversation_id,
        detail={
            "question": question[:200],
            "cacheHit": True,
            "similarity": hit.similarity,
            "ageSeconds": hit.age_seconds,
            "citations": len(hit.citations),
        },
    )

    yield _sse(
        "done",
        {
            "conversationId": conversation_id,
            "messageId": message_id,
            "text": hit.answer,
            "citations": citations,
            "tools": [],
            "cacheHit": True,
            "usage": {
                "inputTokens": 0,
                "outputTokens": 0,
                "cachedInputTokens": 0,
                "estimatedCostUsd": 0.0,
            },
        },
    )


def _public(data: dict[str, Any]) -> dict[str, Any]:
    """Strip anything not JSON-serialisable from a tool's payload.

    `passages` holds dataclasses, which the `sources` event renders separately.
    Everything else a tool returns is already plain data.
    """
    return {key: value for key, value in data.items() if key != "passages"}


def _source(passage: Passage) -> dict[str, Any]:
    return {
        "marker": passage.marker,
        "documentId": passage.document_id,
        "documentTitle": passage.document_title,
        "page": passage.page,
        "section": passage.section,
        "snippet": passage.content[:320],
        "score": round(passage.score, 4),
        "ranks": passage.ranks,
    }


def _citation(citation: Any) -> dict[str, Any]:
    return {
        "marker": citation.marker,
        "chunkId": citation.chunk_id,
        "documentId": citation.document_id,
        "documentTitle": citation.document_title,
        "page": citation.page,
        "section": citation.section,
        "snippet": citation.snippet,
    }


async def _conversation(
    db: AsyncSession, principal: Principal, conversation_id: str | None, question: str
) -> Conversation:
    if conversation_id:
        existing = (
            await db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    # Scoped to the owner in the query. A conversation is more
                    # revealing than the documents behind it — what somebody
                    # asked is not something a colleague of the same role should
                    # be able to read.
                    Conversation.user_id == principal.user_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise NotFoundError("No such conversation.")
        return existing

    conversation = Conversation(id=new_id(), user_id=principal.user_id, title=question[:120])
    db.add(conversation)
    await db.flush()
    return conversation


async def _truncate_from(db: AsyncSession, conversation_id: str, message_id: str) -> None:
    """Editing replaces, rather than branching.

    Everything from the edited message onward is deleted, so the thread has one
    history and the answer below the edit always corresponds to the question
    above it. A branching thread is a better research tool and a worse working
    one.
    """
    target = (
        await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("No such message.")

    await db.execute(
        delete(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at >= target.created_at,
        )
    )


async def _history(db: AsyncSession, conversation_id: str) -> list[Message]:
    rows = (
        await db.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(HISTORY_TURNS)
        )
    ).all()
    return [
        Message(role="assistant" if role == "assistant" else "user", content=content)
        for role, content in reversed(rows)
        if content
    ]


async def _touch(db: AsyncSession, conversation_id: str) -> None:
    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conversation is not None:
        conversation.updated_at = utcnow()


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(db: DbDep, principal: PrincipalDep) -> list[ConversationSummary]:
    rows = (
        await db.execute(
            select(Conversation)
            .where(Conversation.user_id == principal.user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(100)
        )
    ).scalars()
    return [ConversationSummary.model_validate(row) for row in rows]


@router.delete("/conversations", status_code=status.HTTP_204_NO_CONTENT)
async def clear_conversations(db: DbDep, principal: PrincipalDep) -> None:
    """Delete every conversation this employee has.

    Their own, and only their own — the audit trail of what was asked survives
    in `audit_events`, which is a different thing with a different retention
    story and is not the employee's to clear.
    """
    result = cast(
        "CursorResult[Any]",
        await db.execute(delete(Conversation).where(Conversation.user_id == principal.user_id)),
    )
    await audit(
        "conversation.clear",
        resource_type="user",
        resource_id=principal.user_id,
        detail={"deleted": int(result.rowcount or 0)},
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, db: DbDep, principal: PrincipalDep
) -> ConversationDetail:
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == principal.user_id,
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("No such conversation.")

    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars()

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        messages=[
            ChatMessageOut(
                id=row.id,
                role=row.role,
                content=row.content,
                citations=[CitationOut.model_validate(item) for item in json.loads(row.citations)]
                if row.citations
                else [],
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, db: DbDep, principal: PrincipalDep) -> None:
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            delete(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == principal.user_id,
            )
        ),
    )
    if not result.rowcount:
        raise NotFoundError("No such conversation.")
    await audit("conversation.delete", resource_type="conversation", resource_id=conversation_id)
