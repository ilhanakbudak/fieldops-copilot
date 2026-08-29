"""The semantic cache.

Two employees ask "what's the warranty on the radon system" and "how long is the
radon system under warranty" an hour apart. Those are the same question, they
have the same answer, and a system that pays for both twice is paying for
wording.

So an answer is stored against the *embedding* of the question that produced it,
and a later question close enough in that space is served from it. Near-duplicate
questions are the common case in a business where forty people share one manual,
which is what makes this worth having at all.

Three decisions do the work, and the first is the one that matters.

## The cache key includes who is asking

A cached answer was built from the passages the *first* asker was allowed to
see. Serving it to somebody else is serving them a summary of documents they may
have no right to — and it would arrive with citations, looking exactly as
trustworthy as an answer they were entitled to.

So the caller's document audience is part of the key, not a filter applied
afterwards. A salesperson's answer and a technician's answer to the same words
are two different rows. That costs hit rate and it is not negotiable: the whole
of `docs/SECURITY.md` argues that role filtering belongs *inside* the query, and
a cache is a query.

`audience_key` is a sorted, joined list of role names rather than a hash,
because an operator reading this table should be able to see the boundary
working without a lookup table.

## Similarity alone is not safe, and that is measured rather than assumed

Against real `text-embedding-3-small`, on the questions this corpus is written
for:

| | cosine |
|---|---|
| "What does error code E-04 mean?" ↔ "what is error code E-04" | 0.944 |
| "What does error code E-04 mean?" ↔ "E-04 — what does that mean?" | 0.734 |
| "…warranty on a radon water system?" ↔ "How long is the radon system under warranty?" | 0.874 |
| **"…E-04 mean?" ↔ "…E-14 mean?"** | **0.824** |
| **"…regeneration on the NG-4200?" ↔ "…on the NG-6800?"** | **0.905** |

The two distributions overlap. A different fault code scores higher than two
genuine rewordings, and the two softener manuals score higher than all but one.
There is no threshold that admits rewordings and excludes different questions —
which is the same finding as AD-2, arriving in a different place: embeddings
collapse exactly the surface differences that a part number consists of.

So the cache carries the same guard the retriever does. The exact codes in the
question — pulled out by the same regex `analyse.py` uses, no model call — are
part of the key. `E-04` and `E-14` are different rows before their vectors are
compared, and so are the NG-4200 and the NG-6800.

With that guard the threshold can be an ordinary 0.92 rather than a
near-identity 0.98, and what it catches is *rewordings* rather than paraphrases.
That is the conservative choice on purpose: the cost of a miss is one more model
call, and the cost of a wrong hit is a confident wrong answer with citations
attached.

## Only some answers may be cached at all

A cached answer must be a *function of the question*. Most of this assistant's
answers are not:

- Anything the clock touched. "What is today's date" is wrong within a day.
- Anything the CRM or inventory touched. A customer's balance and a bin count
  are current values, and a stale one read aloud on the phone is worse than a
  slow one.
- Anything with conversation history behind it. "And how long does that last?"
  means something different in every conversation.

What is left — a question answered purely out of the document corpus, first turn
of a conversation — is exactly the case worth caching and the case that repeats.
The caller decides; `cacheable()` states the rule so it is in one place.

## A hit is still recorded

`usage_events` gets a row with `cache_hit=True` and zero cost. Without it the
dashboard shows spending falling and cannot say why, and "the cache is working"
and "everyone stopped asking" look identical.

## What this is not

It is not a correctness mechanism. Every entry has a lifetime, because the
corpus changes underneath it: re-tagging a document's audience or re-ingesting
it does not reach in and invalidate anything. On a corpus that changes daily the
lifetime should be hours, and the setting is there to be turned down.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal
from app.core.clock import utcnow
from app.core.ids import new_id
from app.db.models import CachedAnswer
from app.db.roles import audience_key
from app.rag.analyse import extract_codes

logger = logging.getLogger("fieldops.cache")


@dataclass(frozen=True, slots=True)
class CacheHit:
    answer: str
    citations: list[dict[str, object]]
    similarity: float
    age_seconds: int


def cacheable(*, tools_used: list[str], has_history: bool) -> bool:
    """Is this turn's answer a function of the question alone?

    The knowledge base is; a clock and a CRM are not, and a follow-up is a
    function of the conversation it is in. Stated here rather than at the call
    site so there is one place to read the rule and one place to change it.
    """
    if has_history:
        return False
    return bool(tools_used) and set(tools_used) == {"search_knowledge_base"}


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return float(dot / (left_norm * right_norm))


def term_key(question: str) -> str:
    """The exact codes in a question, sorted and joined.

    The same extraction the query analyser uses, and deliberately the regex
    rather than the model: this runs before anything has been paid for, and a
    cache lookup that costs a model call has spent the money it was there to
    save.
    """
    return ",".join(extract_codes(question))


async def lookup(
    session: AsyncSession,
    *,
    question: str,
    embedding: list[float],
    principal: Principal,
    threshold: float,
    ttl: timedelta,
) -> CacheHit | None:
    """The nearest live entry for this audience and these terms, if near enough.

    Scored in Python over the rows for one audience. That is honest about the
    scale this is at — a business's cache of distinct questions is thousands of
    rows, and an exact scan of thousands of short vectors is sub-millisecond —
    and it keeps the behaviour identical on both dialects. A corpus of questions
    large enough to need the HNSW index would use `PgVectorStore`, which is
    already the shape for it.
    """
    cutoff = utcnow() - ttl
    rows = (
        await session.execute(
            select(CachedAnswer).where(
                CachedAnswer.audience_key == audience_key(principal.document_roles),
                # Before the vectors are compared, not after. See above.
                CachedAnswer.term_key == term_key(question),
                CachedAnswer.created_at >= cutoff,
            )
        )
    ).scalars()

    best: tuple[float, CachedAnswer] | None = None
    for row in rows:
        stored = row.embedding
        if not stored:
            continue
        score = _cosine(embedding, stored)
        if best is None or score > best[0]:
            best = (score, row)

    if best is None or best[0] < threshold:
        return None

    score, row = best
    row.hits += 1
    row.last_hit_at = utcnow()

    try:
        citations = json.loads(row.citations or "[]")
    except json.JSONDecodeError:
        citations = []

    return CacheHit(
        answer=row.answer,
        citations=citations if isinstance(citations, list) else [],
        similarity=round(score, 4),
        age_seconds=int((utcnow() - row.created_at).total_seconds()),
    )


async def store(
    session: AsyncSession,
    *,
    question: str,
    embedding: list[float],
    answer: str,
    citations: list[dict[str, object]],
    principal: Principal,
) -> None:
    """Keep this answer for the next person who asks it this way.

    Failures are swallowed. A cache that cannot write is a system that is
    merely slower, and failing an employee's answered question because of it
    would be the wrong trade — the same argument the audit log makes, for a
    much smaller stake.

    Which works because the caller gives this its own transaction. A failed
    flush poisons the session it happened in, so "swallowed" would otherwise
    mean handing the caller a session that refuses every subsequent statement.
    """
    if not answer.strip() or not embedding:
        return

    try:
        session.add(
            CachedAnswer(
                id=new_id(),
                audience_key=audience_key(principal.document_roles),
                term_key=term_key(question)[:300],
                question=question[:1000],
                embedding=embedding,
                answer=answer,
                citations=json.dumps(citations),
                created_at=utcnow(),
            )
        )
        await session.flush()
    except Exception:
        logger.warning("could not store a cached answer", exc_info=True)


async def purge(session: AsyncSession, *, ttl: timedelta) -> int:
    """Drop expired entries. Called on the way past, not on a schedule.

    A background sweeper would be a second thing to run and monitor for a table
    that is small by construction; doing it when the cache is already being
    written keeps the work where the growth is.
    """
    cutoff = utcnow() - ttl
    result = await session.execute(delete(CachedAnswer).where(CachedAnswer.created_at < cutoff))
    # `rowcount` is on the DBAPI cursor result rather than the typed `Result`
    # protocol, so mypy cannot see it on a `DELETE`.
    return int(getattr(result, "rowcount", 0) or 0)
