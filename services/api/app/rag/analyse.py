"""Query analysis.

The first step of retrieval, and the cheapest place to fix the most failures.

Employees do not type search queries. They type "the E-04 thing again on the
Hendersons' unit — what do I check first?", and embedding that verbatim buries
the two words that matter under context the corpus has never seen. So a cheap
model rewrites the question, pulls out exact terms, guesses which kinds of
document are relevant, and classifies the intent.

Three deliberate constraints:

**It runs on the cheap model.** This is short, structured and forgiving work.
Paying the answer model to do it is most of a naive RAG system's bill.

**It cannot fail the request.** A malformed response, a timeout, a provider
outage — all fall back to using the question as written, which is what an
unanalysed pipeline would have done anyway. Retrieval degrades; it does not stop.

**Extracted filters narrow, they never widen.** The model may propose document
types to prefer; it has no say over which *roles* may be searched. That comes
from the caller's principal and nowhere else.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import LlmProvider, Message, Usage

logger = logging.getLogger("fieldops.rag.analyse")

KNOWN_DOC_TYPES = frozenset({"manual", "sop", "warranty", "pricing", "guide", "faq"})

# Error codes and part numbers: two to four letters, an optional dash, digits.
# The one pattern embeddings reliably get wrong and lexical search reliably gets
# right, so it is worth extracting even when the model call fails.
# `\u2013` is an en dash — what a word processor turns "E-04" into, and
# manuals are written in word processors.
_CODE = re.compile(r"\b[A-Za-z]{1,4}[-\u2013]?\d{2,4}\b")

_SYSTEM = """You prepare an employee's question for search over a company's \
internal documents. Reply with JSON only.

{
  "rewritten": "the question as a self-contained search query",
  "keywords": ["exact codes, part numbers or model numbers, verbatim"],
  "doc_types": ["manual" | "sop" | "warranty" | "pricing" | "guide" | "faq"],
  "intent": "lookup" | "question" | "procedure" | "diagnosis"
}

Keep `rewritten` close to the original wording. Put an error code in `keywords` \
exactly as written. Leave `doc_types` empty unless the question clearly names \
one kind of document."""


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    original: str
    rewritten: str
    keywords: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)
    intent: str = "question"
    usage: Usage = field(default_factory=Usage)
    # False when the model call failed and the question is being used as
    # written. Surfaced so a degraded pipeline is visible rather than silent.
    analysed: bool = True

    @property
    def search_text(self) -> str:
        """What actually goes to the search legs.

        The rewritten question with the exact terms appended, because the
        rewrite occasionally drops a code it considered noise — and a code
        dropped here is the one thing the keyword leg existed to catch.
        """
        extras = [word for word in self.keywords if word.lower() not in self.rewritten.lower()]
        return " ".join([self.rewritten, *extras]).strip()


def extract_codes(question: str) -> list[str]:
    return sorted({match.replace("\u2013", "-").upper() for match in _CODE.findall(question)})


async def analyse_query(question: str, provider: LlmProvider) -> QueryAnalysis:
    question = question.strip()
    fallback = QueryAnalysis(
        original=question,
        rewritten=question,
        keywords=extract_codes(question),
        analysed=False,
    )

    try:
        completion = await provider.complete(
            [Message(role="system", content=_SYSTEM), Message(role="user", content=question)],
            max_tokens=256,
        )
        payload = _parse(completion.text)
    except Exception:
        logger.warning("query analysis failed; searching the question as written", exc_info=True)
        return fallback

    if payload is None:
        return fallback

    rewritten = str(payload.get("rewritten") or "").strip() or question
    keywords = _strings(payload.get("keywords"))
    # Union rather than replacement: the regex is dependable and the model is
    # not, and a missed code costs more than a duplicate one.
    keywords = sorted({*keywords, *extract_codes(question)})

    doc_types = [value for value in _strings(payload.get("doc_types")) if value in KNOWN_DOC_TYPES]
    intent = str(payload.get("intent") or "question")

    return QueryAnalysis(
        original=question,
        rewritten=rewritten,
        keywords=keywords,
        doc_types=doc_types,
        intent=intent,
        usage=completion.usage,
    )


def _parse(text: str) -> dict[str, Any] | None:
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


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
