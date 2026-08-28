"""Turning files into pages of text."""

from __future__ import annotations

import io

import pytest

from app.config import get_settings
from app.rag.extract import (
    MarkdownExtractor,
    PdfPlumberExtractor,
    UnsupportedDocumentError,
    find_headings,
    normalise,
    select_extractor,
)
from app.rag.extract.ocr import NullOcrExtractor, should_ocr
from app.rag.ingest import extract
from app.rag.types import ExtractedPage


def _pdf(pages: list[str]) -> bytes:
    """A real PDF, built at test time.

    Asserting against a checked-in binary would test a fixture; building one
    means the assertions are about pdfplumber's actual behaviour.
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER)
    for body in pages:
        y = 720
        for line in body.splitlines():
            pdf.drawString(72, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_markdown_splits_on_horizontal_rules() -> None:
    """Markdown has no pages, so an author needs an explicit way to say where
    one ends — otherwise every citation reads "page 1"."""
    source = b"# Title\n\nFirst.\n\n---\n\n## Second\n\nMore.\n"

    pages = MarkdownExtractor().extract(source, "doc.md")

    assert len(pages) == 2
    assert pages[0].number == 1
    assert "First." in pages[0].text
    assert "More." in pages[1].text


def test_markdown_keeps_its_own_line_breaks() -> None:
    """Unwrapping lines the way PDF text needs would destroy lists."""
    source = b"# Steps\n\n1. Bypass the unit\n2. Close the inlet\n3. Drain\n"

    page = MarkdownExtractor().extract(source, "sop.md")[0]

    assert "1. Bypass the unit\n2. Close the inlet" in page.text


def test_headings_are_recognised_in_three_shapes() -> None:
    """Manufacturer manuals use all three and none of them consistently."""
    found = find_headings(
        "## Error Codes\n"
        "4.2 Brine System Service\n"
        "ROUTINE MAINTENANCE\n"
        "This sentence is ordinary body text that should not be a heading.\n"
    )

    assert "Error Codes" in found
    assert "4.2 Brine System Service" in found
    assert "Routine Maintenance" in found
    assert len(found) == 3


def test_a_shouted_full_sentence_is_not_a_heading() -> None:
    """Page banners are set in capitals too, and turning one into a section
    title drags every following chunk under it."""
    found = find_headings("WARNING DISCONNECT POWER BEFORE SERVICING THIS UNIT COMPLETELY\n")

    assert found == []


def test_normalise_rejoins_words_split_across_lines() -> None:
    """Left alone, a chunk boundary lands mid-word and the embedding is of
    something nobody wrote."""
    assert "regeneration" in normalise("The regen-\neration cycle runs.")


def test_pdf_text_is_extracted_with_page_numbers() -> None:
    data = _pdf(["Error Code E-04", "Brine valve fault"])

    pages = PdfPlumberExtractor().extract(data, "manual.pdf")

    assert [page.number for page in pages] == [1, 2]
    assert "E-04" in pages[0].text
    assert pages[0].source == "pdfplumber"


def test_the_extractor_is_chosen_by_file_type_not_by_configuration() -> None:
    """`PDF_EXTRACTOR` says which PDF library to use. It does not change what a
    `.md` file is."""
    settings = get_settings()

    assert select_extractor(settings, "sop.md").name == "markdown"
    assert select_extractor(settings, "manual.pdf").name == "pdfplumber"


def test_an_unsupported_file_is_refused_before_anything_is_written() -> None:
    with pytest.raises(UnsupportedDocumentError):
        select_extractor(get_settings(), "photo.heic")


def test_a_thin_text_layer_counts_as_needing_ocr() -> None:
    """Not `len(text) == 0`: a scanned page usually still carries a running
    header and a page number, which clear zero without carrying content."""
    scanned = ExtractedPage(number=4, text="NG-4200 Service Manual    4", source="pdfplumber")
    real = ExtractedPage(number=5, text="x" * 200, source="pdfplumber")

    assert should_ocr(scanned, min_chars=96)
    assert not should_ocr(real, min_chars=96)


def test_pages_with_no_text_are_reported_rather_than_ingested_blank() -> None:
    """ "Ready" on a manual with blank pages is a status nobody should trust."""
    settings = get_settings()
    data = _pdf(["Real content here, comfortably past the threshold. " * 6, " "])

    document = extract(data, "manual.pdf", settings)

    assert document.page_count == 2
    assert document.empty_pages == [2]


def test_ocr_is_only_offered_the_pages_that_need_it() -> None:
    """OCR-ing a whole manual to recover three scanned diagrams costs a hundred
    times what the diagrams are worth."""
    asked: list[list[int]] = []

    class RecordingOcr(NullOcrExtractor):
        def extract_pages(
            self, data: bytes, filename: str, pages: list[int]
        ) -> list[ExtractedPage]:
            asked.append(pages)
            return [
                ExtractedPage(number=number, text="recovered by ocr " * 12, source="paddle")
                for number in pages
            ]

    settings = get_settings()
    data = _pdf(["Substantial body text on the first page. " * 8, " "])

    import app.rag.ingest as ingest_module

    original = ingest_module.ocr_extractor_for
    ingest_module.ocr_extractor_for = lambda _settings: RecordingOcr()  # type: ignore[assignment]
    try:
        document = extract(data, "manual.pdf", settings)
    finally:
        ingest_module.ocr_extractor_for = original

    assert asked == [[2]]
    assert document.pages[1].source == "paddle"
    assert document.empty_pages == []


def test_a_failing_ocr_engine_does_not_lose_the_pages_that_worked() -> None:
    class BrokenOcr(NullOcrExtractor):
        def extract_pages(
            self, data: bytes, filename: str, pages: list[int]
        ) -> list[ExtractedPage]:
            raise RuntimeError("engine unavailable")

    settings = get_settings()
    data = _pdf(["Substantial body text on the first page. " * 8, " "])

    import app.rag.ingest as ingest_module

    original = ingest_module.ocr_extractor_for
    ingest_module.ocr_extractor_for = lambda _settings: BrokenOcr()  # type: ignore[assignment]
    try:
        document = extract(data, "manual.pdf", settings)
    finally:
        ingest_module.ocr_extractor_for = original

    assert "Substantial body text" in document.pages[0].text
    assert document.empty_pages == [2]
