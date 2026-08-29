"""Deciding whether to answer, and then answering.

`transcript.py` says when somebody has stopped talking. This says whether what
they said was worth spending money on, and if so, produces the suggestion.

## The classifier earns its keep by refusing

Most of a service call is not a question. It is hello, it is a postcode being
read out twice, it is "bear with me". Running the retrieval pipeline on all of
it would cost roughly the price of a chat turn per sentence, for a page nobody
is reading most of the time — and it would fill the employee's screen with
suggestions to ignore, which is how a live assistant becomes a thing people turn
off.

So a cheap model looks at the last few utterances and answers one question: is
there a problem or a question here that the company's documents could answer? It
returns a self-contained search query when the answer is yes, because "what
about that one?" is not a query and the model has just read the two lines that
say what "that one" is.

Three properties, and they are the same three the query analyser has, for the
same reasons:

**It runs on the cheap model.** A short, structured, forgiving judgement.

**It cannot fail the call.** A timeout or a malformed reply falls back to a
keyword heuristic — a question mark, or one of a small set of words that show up
in problems. The heuristic is worse and it is *cheap*, so the failure mode is a
few unnecessary lookups rather than an assistant that stops working mid-call.

**It never widens what may be searched.** The principal comes from the session
that opened the socket. Nothing the transcript says can change whose documents
are searched — which matters more here than anywhere else in this application,
because the transcript is words spoken by a member of the public.

## The suggestion

Retrieval, then generation, both already built. Two additions.

The customer on the call, when there is one, goes into the prompt as context —
their equipment and the last job. "Why is the water warm since the radon system
went in" is answerable from the manual alone, and answerable *better* when the
prompt also knows this customer has an NG-RN and had it commissioned in March.

And there is a hard ceiling on suggestions per call. A pathological transcript —
a hold tone transcribed as speech, a caller reading a manual aloud — would
otherwise be an unbounded bill attached to a single phone call.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal
from app.config import Settings
from app.connectors import CustomerDetail
from app.llm.base import LlmProvider, Message, Usage
from app.rag.cite import ResolvedAnswer, render_sources, resolve
from app.rag.search.pipeline import Passage, retrieve

logger = logging.getLogger("fieldops.realtime.assist")

_CLASSIFIER_SYSTEM = """You are watching a live phone call at a service business \
and deciding whether the company's internal documents could help. Reply with \
JSON only.

{
  "actionable": true | false,
  "query": "a self-contained search query, or an empty string",
  "reason": "at most eight words, for the employee to read"
}

You are shown the call so far and then the newest lines. Decide about the \
newest lines only — the earlier ones are there so you can resolve what they \
refer to, and they have already been considered.

Set `actionable` to true only for a question or a described problem that a \
manual, procedure or policy could answer. Greetings, scheduling, addresses, \
payment talk and small talk are all false.

Resolve pronouns from the whole transcript: if the newest line is "is that \
meant to happen?" and the line before it describes warm water since a radon \
system was installed, the query is about warm water and radon systems."""

_SUGGEST_SYSTEM = """You are helping an employee who is on the phone right now.

Write at most three short sentences they can say. No preamble, no "you could \
tell them" — write the answer itself. Cite every factual claim with the marker \
of the passage it came from, like [S1]. Only the markers you were shown exist; \
never invent one.

If the passages do not answer it, say so in one sentence. A confident wrong \
answer read aloud to a customer is the worst thing this can do."""

# Words that show up in a problem being described. The fallback when the cheap
# model is unavailable — worse than the model and better than answering
# everything.
_PROBLEM_WORDS = frozenset(
    {
        "broken",
        "code",
        "error",
        "fault",
        "leak",
        "leaking",
        "noise",
        "problem",
        "smell",
        "smells",
        "stopped",
        "warm",
        "warranty",
        "why",
        "wrong",
    }
)


@dataclass(frozen=True, slots=True)
class Actionability:
    actionable: bool
    query: str = ""
    reason: str = ""
    usage: Usage = field(default_factory=Usage)
    # False when the model call failed and the heuristic decided. Surfaced so a
    # degraded assistant is visible rather than silently worse.
    classified: bool = True


def transcript_prompt(context: str, latest: str) -> str:
    """The window, with the part being decided about marked.

    Two labelled sections rather than one blob, so both the model and the
    heuristic can tell which lines are the question and which are the context
    for it.
    """
    earlier = context[: -len(latest)].strip() if latest and context.endswith(latest) else context
    blocks = []
    if earlier:
        blocks.append(f"The call so far:\n{earlier}")
    blocks.append(f"Newest:\n{latest or context}")
    return "\n\n".join(blocks)


async def classify(context: str, llm: LlmProvider, *, latest: str | None = None) -> Actionability:
    """Is there something in the newest lines that the documents could answer?

    `latest` is what has arrived since the last decision. It defaults to the
    whole context, which is right for a one-shot call and wrong for a live one
    — see `TranscriptBuffer.latest`.
    """
    if not context.strip():
        return Actionability(actionable=False, reason="nothing said yet")

    latest = latest if latest is not None else context
    if not latest.strip():
        return Actionability(actionable=False, reason="nothing new said")

    try:
        completion = await llm.complete(
            [
                Message(role="system", content=_CLASSIFIER_SYSTEM),
                Message(role="user", content=transcript_prompt(context, latest)),
            ],
            model=llm.cheap_model,
            max_tokens=160,
        )
        payload = _parse(completion.text)
    except Exception:
        logger.warning("actionability classification failed; using the heuristic", exc_info=True)
        return _heuristic(context, latest)

    if payload is None:
        return _heuristic(context, latest)

    actionable = bool(payload.get("actionable"))
    query = str(payload.get("query") or "").strip()
    if actionable and not query:
        # It said yes and gave nothing to search for. The newest lines are a
        # worse query than the model would have written and a better one than
        # none at all.
        query = _spoken(latest) or context

    return Actionability(
        actionable=actionable,
        query=query,
        reason=str(payload.get("reason") or "").strip()[:80],
        usage=completion.usage,
    )


def _heuristic(context: str, latest: str | None = None) -> Actionability:
    """A question mark, or a word that shows up when something is wrong.

    Read from the *new* lines, for the same reason the model is told to: a
    problem already answered would otherwise be answered again every time the
    caller said anything at all.

    The query is the new lines plus the one before them, because a problem
    described across a breath — "the water's been warm" / "ever since the radon
    system went in" — is not searchable from either half.
    """
    new = _spoken(latest if latest is not None else context)
    if not new:
        return Actionability(actionable=False, reason="nothing new said", classified=False)

    words = {word.lower() for word in re.findall(r"[a-z']+", new.lower())}
    hits = words & _PROBLEM_WORDS
    if not (new.rstrip().endswith("?") or hits):
        return Actionability(actionable=False, reason="not a question", classified=False)

    lines = [line for line in context.splitlines() if line.strip()]
    query = _spoken("\n".join(lines[-2:])) if len(lines) > 1 else new
    return Actionability(
        actionable=True,
        query=query or new,
        reason=f"mentions {sorted(hits)[0]}" if hits else "a question was asked",
        classified=False,
    )


def _spoken(block: str) -> str:
    """The words, without the speaker labels we put there.

    "Caller" is our prefix, not theirs, and matching a keyword against it would
    make every line about a caller actionable.
    """
    return " ".join(
        line.split(":", 1)[-1].strip() for line in block.splitlines() if line.strip()
    ).strip()


def _parse(text: str) -> dict[str, object] | None:
    """Read the JSON, tolerating a model that wrapped it in prose or a fence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class Suggestion:
    """One suggestion, as it is being written."""

    query: str
    reason: str
    passages: list[Passage] = field(default_factory=list)
    text: str = ""
    usage: Usage = field(default_factory=Usage)

    def resolved(self) -> ResolvedAnswer:
        return resolve(self.text, self.passages)


async def suggest(
    session: AsyncSession,
    *,
    query: str,
    reason: str,
    context: str,
    principal: Principal,
    llm: LlmProvider,
    settings: Settings,
    customer: CustomerDetail | None = None,
) -> AsyncIterator[Suggestion]:
    """Retrieve, then write. Yields the suggestion as it grows.

    The principal is the employee whose session opened the socket, and it is the
    only thing that decides which documents are searched. The caller is a member
    of the public speaking into a telephone; nothing they say reaches this
    argument.
    """
    result = await retrieve(
        session,
        query,
        principal,
        settings=settings,
        llm=llm,
        top_k=settings.assist_top_k,
    )

    suggestion = Suggestion(
        query=query, reason=reason, passages=result.passages, usage=result.usage
    )
    yield suggestion

    if not result.passages:
        suggestion.text = "Nothing in the documents you can read answers that."
        yield suggestion
        return

    messages = [
        Message(role="system", content=_SUGGEST_SYSTEM),
        Message(role="user", content=_prompt(context, result.passages, customer)),
    ]

    async for event in llm.stream(messages):
        if event.delta:
            suggestion.text += event.delta
            yield suggestion
        if event.usage:
            suggestion.usage = suggestion.usage + event.usage

    yield suggestion


def _prompt(context: str, passages: list[Passage], customer: CustomerDetail | None) -> str:
    """Passages, then this customer, then the call — in that order.

    Stable prefix first: the passages for one question do not change between the
    first token and the last, and prompt caching keys on an exact prefix. The
    transcript is the part that grows, so it goes at the end.
    """
    blocks = [render_sources(passages)]

    if customer is not None:
        blocks.append(_customer_block(customer))

    blocks.append(f"The call so far:\n{context}\n\nWhat should the employee say?")
    return "\n\n---\n\n".join(blocks)


def _customer_block(customer: CustomerDetail) -> str:
    """What this customer has, and what happened last time.

    Not the whole record. An open balance and an estimate are the office's
    business and not the answer to a technical question, and every line here is
    a line the model has to read past.
    """
    lines = [f"You are speaking to {customer.customer.name} ({customer.customer.id})."]

    if customer.equipment:
        lines.append("Installed:")
        lines.extend(
            f"- {item.model}, serial {item.serial}, installed {item.installed_on}"
            for item in customer.equipment[:4]
        )

    if customer.jobs:
        last = customer.jobs[0]
        lines.append(f"Last visit {last.date}: {last.summary}.")
        if last.notes:
            lines.append(f"Technician's note: {last.notes}")

    return "\n".join(lines)
