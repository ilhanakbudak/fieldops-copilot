"""A provider that needs no credentials.

**This is extractive, not generative.** It does not paraphrase, infer or
summarise: it selects the sentences from the retrieved passages that best match
the question, and attaches the citation markers for the passages it took them
from. What it demonstrates is the *pipeline* — tool routing, retrieval, role
filtering, citation resolution, streaming, cost accounting — with a stand-in
where the model would be.

**It routes tools by rule.** Given a toolset it picks one by matching the
question against each tool's keywords, which is a crude imitation of what a real
model does with a tool description. Crude, and enough to demonstrate the thing
that matters: asked the date it calls the clock, asked about a fault code it
searches the manuals, and asked who a customer is it queries the CRM. The
routing is deliberately visible in `_route` rather than hidden, because a
reviewer should be able to see exactly how much of this is real.

That distinction is worth being blunt about in a public repository, because the
alternative is a reviewer running the demo, seeing fluent prose, and believing
they are looking at a language model. They are looking at sentence selection.
Point `LLM_PROVIDER=openai` at a real key and the same pipeline produces real
answers.

It is also genuinely useful beyond the demo: the tests use it, so the retrieval
and citation assertions are about retrieval and citations rather than about
whatever a model happened to say that day.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from app.llm.base import Completion, Message, StreamEvent, ToolCall, ToolSpec, Usage

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
_WORD = re.compile(r"[a-z0-9][a-z0-9\-/.]*")
_MARKER = re.compile(r"\[S(\d+)\]")
_CODE_LIKE = re.compile(r"^[a-z]{1,4}-\d{2,4}$")
_PERSON = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")
_ACCOUNT = re.compile(r"\bNG-\d{3,6}\b")

# Words that appear in every question and discriminate between nothing.
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "can",
        "should",
        "would",
        "could",
    ]
)

# Passages arrive in a tool result under these markers; the answer has to carry
# them back so the API can resolve them to real documents.
_SOURCE_BLOCK = re.compile(r"\[S(\d+)\][^\n]*\n(.*?)(?=\n\[S\d+\]|\Z)", re.DOTALL)


class MockLlmProvider:
    name = "mock"
    chat_model = "mock"
    cheap_model = "mock"

    async def complete(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
    ) -> Completion:
        prompt = messages[-1].content if messages else ""

        # Query analysis asks for JSON. Answering in the right shape keeps the
        # analysis step exercised in demo mode rather than skipped.
        if "JSON" in (messages[0].content if messages else ""):
            return Completion(text=_analyse(prompt), usage=_usage(messages, 40))

        return Completion(text=_answer(messages), usage=_usage(messages, 80))

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if tools:
            call = _route(messages, tools)
            if call is not None:
                yield StreamEvent(tool_calls=(call,))
                yield StreamEvent(usage=_usage(messages, 0))
                return

        text = _answer(messages)
        # Word by word, so the client's streaming path is exercised rather than
        # handed one large chunk that hides a broken renderer.
        words = text.split(" ")
        for index, word in enumerate(words):
            yield StreamEvent(delta=word if index == 0 else f" {word}")
        yield StreamEvent(usage=_usage(messages, len(words)))


def _without_headings(block: str) -> str:
    lines = [line for line in block.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines)


def _clean(sentence: str) -> str:
    """Strip the markdown a real model would never echo.

    The passages are stored as they were written, headings and emphasis
    included. A generative model reads that structure and writes prose; this one
    copies sentences, so the markup has to come off here or the answer reads as
    a paste from a file rather than as an answer.
    """
    sentence = " ".join(sentence.split())
    sentence = re.sub(r"^#{1,6}\s*", "", sentence)
    sentence = re.sub(r"\*\*(.+?)\*\*", r"\1", sentence)
    sentence = re.sub(r"`(.+?)`", r"\1", sentence)
    return sentence.strip()


# Which words send a question to which tool. A real model reads the tool's
# description; this reads a keyword list. Both are guesses — the difference is
# that a model's guess generalises and this one does not, which is the honest
# limit of a stand-in.
_ROUTES: list[tuple[str, frozenset[str]]] = [
    (
        "get_current_time",
        frozenset({"time", "date", "today", "now", "day", "clock", "timezone"}),
    ),
    (
        "find_customer",
        frozenset({"customer", "pull", "account", "client", "phone", "address", "who"}),
    ),
    (
        "get_customer_detail",
        frozenset({"history", "jobs", "installed", "equipment", "estimate", "invoice", "balance"}),
    ),
]


def _route(messages: list[Message], tools: list[ToolSpec]) -> ToolCall | None:
    """Pick a tool, or none at all.

    Returning `None` is the interesting branch: it means "answer from what you
    already have", which after a tool has run is usually what should happen. A
    stand-in that always called a tool would loop forever.
    """
    available = {tool.name for tool in tools}
    results = [message for message in messages if message.role == "tool"]

    if results:
        # One chain, so the loop is demonstrably a loop rather than a single
        # dispatch: a customer search that returned exactly one account is
        # followed by pulling that account's record, which is what the model
        # does with "pull up X and tell me what we installed".
        follow_up = _chain(results[-1].content, available)
        return follow_up

    question = next(
        (message.content for message in reversed(messages) if message.role == "user"), ""
    )
    words = _tokens(question)

    # A two-word proper noun is almost always a person, and a question naming a
    # person is a question about a customer. Checked before the keyword routes
    # because "what did we install for John Smith" contains no CRM keyword at
    # all — a real model reads the tool descriptions and gets this for free.
    if "find_customer" in available and _PERSON.search(question):
        return ToolCall(
            id="call_find_customer",
            name="find_customer",
            arguments=_arguments("find_customer", question),
        )

    for suffix, triggers in _ROUTES:
        # Suffix, not equality: an MCP tool arrives namespaced by its server, so
        # the clock is `mcp_time_get_current_time` rather than
        # `get_current_time`. A real model reads the description and does not
        # care what the tool is called.
        match = next((name for name in available if name.endswith(suffix)), None)
        if match and words & triggers:
            return ToolCall(id=f"call_{suffix}", name=match, arguments=_arguments(suffix, question))

    if "search_knowledge_base" in available:
        return ToolCall(
            id="call_search",
            name="search_knowledge_base",
            arguments={"query": question},
        )
    return None


def _chain(previous: str, available: set[str]) -> ToolCall | None:
    """The second step, when the first produced exactly one account."""
    if "get_customer_detail" not in available or "More than one" in previous:
        return None

    accounts = _ACCOUNT.findall(previous)
    if len(accounts) != 1:
        return None

    return ToolCall(
        id="call_get_customer_detail",
        name="get_customer_detail",
        arguments={"customer_id": accounts[0]},
    )


def _arguments(name: str, question: str) -> dict[str, object]:
    if name == "get_current_time":
        # A real model reads the business timezone out of the system prompt and
        # fills it in. This reads it from the same setting, so the demo answers
        # in the same timezone the deployment is configured for.
        from app.config import get_settings

        return {"timezone": get_settings().business_timezone}
    if name in {"find_customer", "get_customer_detail"}:
        # Everything that looks like a proper noun. Crude, and it recovers
        # "John Smith" out of "pull up John Smith in Portland", which is the
        # case worth demonstrating.
        names = re.findall(r"\b[A-Z][a-z]{2,}\b", question)
        skip = {"Pull", "What", "Tell", "Show", "Find", "Who", "When", "Where", "Does", "Did"}
        query = " ".join(word for word in names if word not in skip)
        return {"query": query or question}
    return {"query": question}


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOP}


def _analyse(prompt: str) -> str:
    """Rule-based stand-in for the query-analysis call.

    Deliberately simple, and deliberately good at the one thing that matters
    most: pulling out an exact code or part number, which is what the keyword
    leg of the hybrid search needs and what embeddings are worst at.
    """
    question = prompt.strip().splitlines()[-1] if prompt.strip() else ""
    # An en dash is what a word processor turns "E-04" into, and manuals are
    # written in word processors. `noqa`: the ambiguity is the point.
    codes = re.findall(r"\b[A-Z]{1,4}[-\u2013]?\d{2,4}\b", question)

    doc_types: list[str] = []
    lowered = question.lower()
    for word, doc_type in (
        ("warranty", "warranty"),
        ("cost", "pricing"),
        ("price", "pricing"),
        ("margin", "pricing"),
        ("procedure", "sop"),
        ("install", "sop"),
    ):
        if word in lowered and doc_type not in doc_types:
            doc_types.append(doc_type)

    return json.dumps(
        {
            "rewritten": question,
            "keywords": sorted({code.replace("\u2013", "-") for code in codes}),
            "doc_types": doc_types,
            "intent": "lookup" if codes else "question",
        }
    )


def _answer(messages: list[Message]) -> str:
    """Select the sentences that best match the question, with their markers.

    Reads the *tool results*, not the system prompt. An earlier version scanned
    the system message for marker blocks, which worked until the prompt itself
    grew an example citation — and then every answer was an extract from the
    instructions. Tool output is the only place passages legitimately appear.
    """
    question = next(
        (message.content for message in reversed(messages) if message.role == "user"), ""
    )
    results = [message.content for message in messages if message.role == "tool"]
    if not results:
        return (
            "I do not have anything to answer that from. Try asking about the "
            "company's documents, or about a customer."
        )

    transcript = "\n\n".join(results)
    wanted = _tokens(question)

    scored: list[tuple[float, int, str]] = []
    for match in _SOURCE_BLOCK.finditer(transcript):
        index = int(match.group(1))
        # Heading lines are dropped before splitting. A heading has no sentence
        # terminator, so it would otherwise be glued to the sentence beneath it
        # and the answer would read "E-14 Reserve Capacity Exceeded Distinct
        # from E-04…" — a paste from a file rather than a reply.
        for sentence in _SENTENCE.split(_without_headings(match.group(2))):
            sentence = _clean(sentence)
            if len(sentence) < 40:
                continue
            overlap = wanted & _tokens(sentence)
            # Two content words, or one exact code. A single incidental word in
            # common — "error" appearing in "specification error" — is not
            # evidence of anything, and selecting on it produces exactly the
            # failure the system prompt tells a real model to avoid: a confident
            # answer assembled from passages that do not address the question.
            if len(overlap) < 2 and not any(_CODE_LIKE.match(word) for word in overlap):
                continue
            # Normalise by length so a long paragraph does not win on volume.
            score = len(overlap) / (len(_tokens(sentence)) ** 0.5 or 1)
            scored.append((score, index, sentence))

    if not scored:
        # Two different situations, and collapsing them would be the same
        # mistake the relevance floor exists to prevent.
        if _SOURCE_BLOCK.search(transcript):
            # Passages came back and none of them address the question — which
            # is what a role boundary looks like from the inside.
            return (
                "I could not find anything in the documents you have access to that "
                "answers this. It may be in a document written for another role."
            )
        # No marked passages at all: the tool that ran was not the knowledge
        # base — a clock, or a customer lookup. Its output is the answer.
        #
        # The *last* result, not the whole transcript: a chained lookup produces
        # a search result and then the record it led to, and relaying both makes
        # the answer repeat itself.
        return _relay(results[-1])

    scored.sort(key=lambda row: -row[0])
    chosen = scored[:3]

    # Keep the passages in source order in the answer, so the citations read in
    # the order a person would encounter them in the documents.
    chosen.sort(key=lambda row: (row[1], -row[0]))

    parts = [f"{sentence} [S{index}]" for _score, index, sentence in chosen]
    return " ".join(parts)


def _relay(output: str) -> str:
    """Pass a non-knowledge tool's output through.

    A real model would write a sentence around this. A stand-in that tried to
    would be inventing prose, which is the one thing this provider exists not to
    do — so it relays, and the README says why.

    Formatting is not paraphrasing: a JSON result is fenced so the interface
    renders it as a code block rather than as a sentence that happens to contain
    braces. What it says is untouched.
    """
    text = output.strip()
    if not text:
        return "The tool returned nothing."

    if text.startswith("{") or text.startswith("["):
        try:
            json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return f"```json\n{text}\n```"

    if len(text) > 1200:
        text = text[:1200].rsplit("\n", 1)[0] + "\n…"
    return text


def _usage(messages: list[Message], output_words: int) -> Usage:
    """A rough token count, so the cost path is exercised end to end.

    Four characters per token is the usual English approximation. It costs
    nothing either way — the mock provider is priced at zero — but a usage row
    of all zeroes would hide a broken accounting path until the day a real key
    is configured.
    """
    input_chars = sum(len(message.content) for message in messages)
    return Usage(input_tokens=input_chars // 4, output_tokens=int(output_words * 1.3))
