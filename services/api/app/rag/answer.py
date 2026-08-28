"""Prompt assembly and generation.

The prompt is built in a fixed order, and the order is not stylistic:

    system rules  →  retrieved passages  →  conversation  →  question

Prompt caching keys on an exact prefix match. Putting the stable parts first —
the rules, then the passages, which are identical for a repeated question —
means a follow-up in the same conversation re-reads a cached prefix at a
fraction of the input price. Moving the question above the passages for
readability would silently disable that, which is why the assembly lives in one
function and says so here.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.llm.base import LlmProvider, Message, Usage
from app.rag.cite import ResolvedAnswer, render_sources, resolve
from app.rag.search.pipeline import Passage, RetrievalResult

logger = logging.getLogger("fieldops.rag.answer")

SYSTEM_RULES = """You answer questions for employees of a service business, \
using only the passages provided below.

Rules:
- Answer only from the passages. If they do not contain the answer, say so \
plainly and stop. Do not fall back on general knowledge — a plausible answer \
about someone else's equipment is worse than no answer.
- Cite every factual claim with the marker of the passage it came from, like \
[S1]. Put the marker at the end of the sentence it supports.
- Never invent a marker. Only the markers listed below exist.
- Be brief and concrete. The person reading this usually has a customer waiting.
- If the passages disagree, say so and cite both.
"""

NO_CONTEXT = (
    "I could not find anything in the documents you have access to that answers this. "
    "It may be in a document written for another role, or it may not be in the "
    "knowledge base yet."
)


@dataclass(frozen=True, slots=True)
class AnswerChunk:
    """One event on the way to an answer."""

    delta: str = ""
    done: ResolvedAnswer | None = None
    usage: Usage | None = None


@dataclass(frozen=True, slots=True)
class Answer:
    resolved: ResolvedAnswer
    passages: list[Passage] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0


def build_messages(
    question: str,
    passages: list[Passage],
    history: list[Message] | None = None,
) -> list[Message]:
    """Stable prefix first. See the module docstring before reordering this."""
    system = f"{SYSTEM_RULES}\n---\n\n{render_sources(passages)}"

    messages = [Message(role="system", content=system)]
    if history:
        # Trimmed by the caller. The whole point of the prefix ordering is that
        # what varies sits at the end.
        messages.extend(history)
    messages.append(Message(role="user", content=question))
    return messages


async def stream_answer(
    question: str,
    retrieval: RetrievalResult,
    provider: LlmProvider,
    *,
    history: list[Message] | None = None,
) -> AsyncIterator[AnswerChunk]:
    """Stream the answer, then resolve its citations.

    Text is streamed raw, markers and all, and resolved once at the end. The
    alternative — resolving as tokens arrive — means parsing a marker that may
    still be half-written, and the client would have to re-render text it had
    already shown.
    """
    started = time.perf_counter()

    if not retrieval.passages:
        # No passages means no grounding, and asking a model to answer without
        # grounding is how a retrieval system starts inventing warranty terms.
        yield AnswerChunk(delta=NO_CONTEXT)
        yield AnswerChunk(
            done=ResolvedAnswer(text=NO_CONTEXT, citations=[], dropped=[]),
            usage=Usage(),
        )
        return

    messages = build_messages(question, retrieval.passages, history)
    parts: list[str] = []
    usage = Usage()

    async for event in provider.stream(messages):
        if event.delta:
            parts.append(event.delta)
            yield AnswerChunk(delta=event.delta)
        if event.usage:
            usage = usage + event.usage

    resolved = resolve("".join(parts), retrieval.passages)
    if resolved.dropped:
        logger.warning(
            "model cited %d marker(s) that were never retrieved: %s",
            len(resolved.dropped),
            ", ".join(sorted(set(resolved.dropped))),
        )

    yield AnswerChunk(done=resolved, usage=usage)
    logger.debug("answer generated in %dms", int((time.perf_counter() - started) * 1000))
