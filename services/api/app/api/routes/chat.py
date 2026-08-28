"""The chat endpoint.

Server-sent events rather than a WebSocket. The traffic is one-directional —
the question goes up in the POST, the answer comes down — and SSE is a plain
HTTP response, so it inherits the session cookie, the proxy configuration and
the audit middleware without any of them being special-cased. A WebSocket would
need its own authentication path, which is one more place to get authorisation
wrong. The live-call feature genuinely is bidirectional and will use one; this
is not.

The event sequence:

    sources   the passages retrieved, before any text
    delta     answer text, as it arrives
    done      resolved citations, token usage and cost
    error     something failed mid-stream

`sources` goes first on purpose. A reader watching an answer form can see what
it is being drawn from, and when the answer is wrong they can see immediately
whether retrieval or generation was at fault.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.llm import Message, cost_usd, get_llm_provider
from app.rag.answer import stream_answer
from app.rag.search.pipeline import retrieve

logger = logging.getLogger("fieldops.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

# How much of a conversation is replayed to the model. Enough to resolve "what
# about the other one?", short enough that a long thread does not quietly become
# the most expensive part of every request.
HISTORY_TURNS = 6


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

    try:
        async with session_scope(principal) as db:
            conversation = await _conversation(db, principal, body.conversation_id, question)
            conversation_id = conversation.id
            history = await _history(db, conversation_id)

            retrieval = await retrieve(db, question, principal, settings=settings, llm=llm)

            db.add(
                ChatMessage(
                    id=new_id(),
                    conversation_id=conversation_id,
                    role="user",
                    content=question,
                    created_at=utcnow(),
                )
            )

        yield _sse(
            "sources",
            {
                "conversationId": conversation_id,
                "sources": [
                    {
                        "marker": passage.marker,
                        "documentId": passage.document_id,
                        "documentTitle": passage.document_title,
                        "page": passage.page,
                        "section": passage.section,
                        "snippet": passage.content[:320],
                        "score": round(passage.score, 4),
                        "ranks": passage.ranks,
                    }
                    for passage in retrieval.passages
                ],
                "retrievalMs": retrieval.duration_ms,
                "candidates": retrieval.candidates,
                "reranker": retrieval.reranker,
                "rewritten": retrieval.analysis.rewritten,
            },
        )

        answer = None
        usage = retrieval.usage
        async for chunk in stream_answer(question, retrieval, llm, history=history):
            if chunk.delta:
                yield _sse("delta", {"text": chunk.delta})
            if chunk.usage:
                usage = usage + chunk.usage
            if chunk.done:
                answer = chunk.done

        if answer is None:  # pragma: no cover - the generator always ends with `done`
            raise RuntimeError("the answer stream ended without a result")

        cost = cost_usd(llm.chat_model, usage)

        async with session_scope(principal) as db:
            message_id = new_id()
            db.add(
                ChatMessage(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer.text,
                    citations=json.dumps(
                        [
                            {
                                "marker": citation.marker,
                                "chunkId": citation.chunk_id,
                                "documentId": citation.document_id,
                                "documentTitle": citation.document_title,
                                "page": citation.page,
                                "section": citation.section,
                                "snippet": citation.snippet,
                            }
                            for citation in answer.citations
                        ]
                    ),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    latency_ms=retrieval.duration_ms,
                    created_at=utcnow(),
                )
            )
            await _touch(db, conversation_id)

        await record_usage(
            feature="chat",
            provider=llm.name,
            model=llm.chat_model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cost_usd=cost,
            latency_ms=retrieval.duration_ms,
            principal=principal,
        )
        await audit(
            "ai.answer",
            principal=principal,
            resource_type="conversation",
            resource_id=conversation_id,
            detail={
                "question": question[:200],
                "passages": len(retrieval.passages),
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
                "citations": [
                    {
                        "marker": citation.marker,
                        "chunkId": citation.chunk_id,
                        "documentId": citation.document_id,
                        "documentTitle": citation.document_title,
                        "page": citation.page,
                        "section": citation.section,
                        "snippet": citation.snippet,
                    }
                    for citation in answer.citations
                ],
                "usage": {
                    "inputTokens": usage.input_tokens,
                    "outputTokens": usage.output_tokens,
                    "cachedInputTokens": usage.cached_input_tokens,
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

    conversation = Conversation(
        id=new_id(),
        user_id=principal.user_id,
        title=question[:120],
    )
    db.add(conversation)
    await db.flush()
    return conversation


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
            .limit(50)
        )
    ).scalars()
    return [ConversationSummary.model_validate(row) for row in rows]


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
                Conversation.id == conversation_id, Conversation.user_id == principal.user_id
            )
        ),
    )
    if not result.rowcount:
        raise NotFoundError("No such conversation.")
    await audit("conversation.delete", resource_type="conversation", resource_id=conversation_id)
