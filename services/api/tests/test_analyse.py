"""Query analysis, and what happens when it fails."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.llm.base import Completion, Message, StreamEvent, Usage
from app.llm.mock import MockLlmProvider
from app.rag.analyse import analyse_query, extract_codes


class BrokenProvider:
    """A provider that is down."""

    name = "broken"
    chat_model = "broken"
    cheap_model = "broken"

    async def complete(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
    ) -> Completion:
        raise RuntimeError("provider unavailable")

    async def stream(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 1024
    ) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("provider unavailable")
        yield StreamEvent()  # pragma: no cover


class NonsenseProvider(BrokenProvider):
    """A provider that answers, but not with JSON."""

    async def complete(
        self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
    ) -> Completion:
        return Completion(text="Sure! Here is what I think you meant.", usage=Usage())


def test_codes_are_extracted_from_the_raw_question() -> None:
    """The one pattern embeddings reliably get wrong."""
    assert extract_codes("what does error code E-04 mean") == ["E-04"]
    assert extract_codes("the NG-4200 is showing E-14 again") == ["E-14", "NG-4200"]
    assert extract_codes("why is the water warm") == []


def test_an_en_dash_is_treated_as_a_hyphen() -> None:
    """Word processors turn a hyphen into an en dash, and manuals are written in
    word processors."""
    en_dash = "\u2013"
    assert extract_codes(f"error code E{en_dash}04") == ["E-04"]


async def test_analysis_pulls_out_codes_and_document_types() -> None:
    analysis = await analyse_query("What is the warranty on error code E-04?", MockLlmProvider())

    assert "E-04" in analysis.keywords
    assert "warranty" in analysis.doc_types
    assert analysis.analysed


async def test_a_failing_provider_degrades_to_the_question_as_written() -> None:
    """Retrieval degrades; it does not stop. The regex still recovers the code,
    which is the part that mattered."""
    analysis = await analyse_query("what does E-04 mean", BrokenProvider())

    assert analysis.analysed is False
    assert analysis.rewritten == "what does E-04 mean"
    assert analysis.keywords == ["E-04"]


async def test_an_unparseable_response_degrades_the_same_way() -> None:
    analysis = await analyse_query("what does E-04 mean", NonsenseProvider())

    assert analysis.analysed is False
    assert analysis.keywords == ["E-04"]


async def test_search_text_restores_a_code_the_rewrite_dropped() -> None:
    """A rewrite that decides an error code is noise deletes the one term the
    keyword leg existed to catch."""
    from app.rag.analyse import QueryAnalysis

    analysis = QueryAnalysis(
        original="what does E-04 mean",
        rewritten="meaning of the brine valve fault",
        keywords=["E-04"],
    )

    assert "E-04" in analysis.search_text


async def test_a_model_proposed_document_type_it_does_not_know_is_ignored() -> None:
    """Extracted filters narrow the search. An unrecognised one would narrow it
    to nothing."""

    class InventiveProvider(BrokenProvider):
        async def complete(
            self, messages: list[Message], *, model: str | None = None, max_tokens: int = 512
        ) -> Completion:
            return Completion(
                text='{"rewritten": "q", "keywords": [], "doc_types": ["invoices"], '
                '"intent": "lookup"}',
                usage=Usage(),
            )

    analysis = await analyse_query("q", InventiveProvider())

    assert analysis.doc_types == []
