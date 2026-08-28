"""Splitting a document into what retrieval searches."""

from __future__ import annotations

from app.config import get_settings
from app.rag.chunk import chunk_document, parent_sections
from app.rag.types import ExtractedDocument, ExtractedPage


def _document(text: str, headings: list[str] | None = None) -> ExtractedDocument:
    page = ExtractedPage(number=1, text=text, source="markdown", headings=headings or [])
    return ExtractedDocument(pages=[page])


def test_a_short_document_is_one_chunk() -> None:
    chunks = chunk_document(_document("A single short paragraph."), get_settings())

    assert len(chunks) == 1
    assert chunks[0].ordinal == 0


def test_chunks_stay_under_the_budget() -> None:
    settings = get_settings()
    body = " ".join(f"Sentence number {index} about the brine valve." for index in range(200))

    chunks = chunk_document(_document(body), settings)

    assert len(chunks) > 1
    # The budget is a target, not a hard cut: an unsplittable sentence longer
    # than the budget is taken whole rather than cut mid-clause.
    assert all(len(chunk.content) <= settings.chunk_chars * 1.2 for chunk in chunks)


def test_an_oversized_sentence_is_kept_whole() -> None:
    """One oversized chunk is a better failure than two meaningless halves — a
    table row or a long parameter list has no sentence boundary to cut on."""
    settings = get_settings()
    monster = "Part " + ", ".join(f"NG-{index:04d}" for index in range(300)) + "."

    chunks = chunk_document(_document(monster), settings)

    assert len(chunks) == 1
    assert chunks[0].content.startswith("Part NG-0000")


def test_chunks_carry_the_heading_they_sit_under() -> None:
    """Which is what makes a citation say "E-04 Brine Valve Fault" rather than
    just naming the manual."""
    text = (
        "## Error Codes\n\n"
        "Codes clear when the fault is resolved.\n\n"
        "### E-04 Brine Valve Fault\n\n"
        "The brine draw completed without the expected drop in level.\n"
    )

    chunks = chunk_document(
        _document(text, headings=["Error Codes", "E-04 Brine Valve Fault"]), get_settings()
    )

    sections = [chunk.section for chunk in chunks]
    assert "Error Codes" in sections
    assert "E-04 Brine Valve Fault" in sections


def test_a_heading_is_never_a_chunk_on_its_own() -> None:
    """Splitting on blank lines strands a heading as its own block. Emitted as a
    chunk it embeds beautifully against a query about that heading and contains
    no answer — a confident, useless result, and the most common way a naive
    chunker degrades retrieval."""
    text = (
        "## Error Codes\n\n"
        "### E-04 Brine Valve Fault\n\n"
        "The brine draw cycle completed without the expected drop in level. "
        "Check for a salt bridge before replacing the valve.\n"
    )

    chunks = chunk_document(
        _document(text, headings=["Error Codes", "E-04 Brine Valve Fault"]), get_settings()
    )

    for chunk in chunks:
        body = chunk.content.replace("#", "").strip()
        assert body not in {"Error Codes", "E-04 Brine Valve Fault"}, chunk.content

    e04 = next(chunk for chunk in chunks if "E-04" in chunk.content)
    assert "salt bridge" in e04.content


def test_a_heading_starts_a_new_parent_section() -> None:
    """Without this a section boundary can fall inside a parent, and the model
    reads two unrelated topics as one passage."""
    text = (
        "## Regeneration\n\nBackwash lifts the resin bed.\n\n"
        "## Resin Replacement\n\nExpect eight to twelve years on municipal water.\n"
    )

    chunks = chunk_document(
        _document(text, headings=["Regeneration", "Resin Replacement"]), get_settings()
    )

    parents = {chunk.section: chunk.parent_index for chunk in chunks}
    assert parents["Regeneration"] != parents["Resin Replacement"]


def test_a_section_running_onto_the_next_page_is_still_that_section() -> None:
    """The heading is threaded across the page break rather than reset.

    The merged unit is attributed to the page it *starts* on — which is where a
    reader following the citation should begin, and is at most one chunk's worth
    of text away from the rest of it.
    """
    settings = get_settings()
    document = ExtractedDocument(
        pages=[
            ExtractedPage(
                number=1,
                text="## Warm Water After Installation\n\nThe tank holds forty gallons.",
                source="markdown",
                headings=["Warm Water After Installation"],
            ),
            ExtractedPage(
                number=2,
                text="Customers describe this as the cold water being warm.",
                source="markdown",
                headings=[],
            ),
        ]
    )

    chunks = chunk_document(document, settings)

    assert all(chunk.section == "Warm Water After Installation" for chunk in chunks)
    assert "cold water being warm" in " ".join(chunk.content for chunk in chunks)


def test_parents_reassemble_without_repeating_the_overlap() -> None:
    """Chunks overlap so a fact split across a boundary stays retrievable from
    either side. Concatenating them naively makes the model read the same
    sentence twice and treat the repetition as emphasis."""
    settings = get_settings()
    body = " ".join(f"Step {index} of the regeneration cycle." for index in range(120))

    chunks = chunk_document(_document(body), settings)
    parents = parent_sections(chunks)

    assembled = " ".join(section.content for section in parents.values())
    assert assembled.count("Step 40 of the regeneration cycle.") == 1


def test_empty_pages_contribute_nothing() -> None:
    document = ExtractedDocument(
        pages=[
            ExtractedPage(number=1, text="", source="pdfplumber"),
            ExtractedPage(number=2, text="Real content.", source="pdfplumber"),
        ],
        empty_pages=[1],
    )

    chunks = chunk_document(document, get_settings())

    assert [chunk.page for chunk in chunks] == [2]
