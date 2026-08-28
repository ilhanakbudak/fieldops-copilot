"""The extraction boundary.

One protocol, several implementations, chosen by configuration:

    PdfPlumberExtractor   MIT, the default
    PyMuPdfExtractor      faster, AGPL-3.0, opt-in extra
    MarkdownExtractor     for the synthetic corpus, and for SOPs that were never PDFs
    PaddleOcrExtractor    for pages with no text layer

The interesting part is not any one of them. It is that a *page* is the unit,
which is what lets a scanned insert inside an otherwise digital manual fall
through to OCR without the whole document taking the slow path — and what makes
"which pages needed OCR" answerable afterwards.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from app.rag.types import ExtractedPage


@runtime_checkable
class TextExtractor(Protocol):
    """Turns bytes into pages of text."""

    name: str

    def supports(self, filename: str, content_type: str | None) -> bool: ...

    def extract(self, data: bytes, filename: str) -> list[ExtractedPage]: ...


@runtime_checkable
class OcrExtractor(Protocol):
    """Reads specific pages of a document that have no text layer.

    Separate from `TextExtractor` because it is asked for a subset of pages, not
    a whole file. Running OCR over a 400-page manual to recover three scanned
    diagrams would cost a hundred times what the three pages are worth.
    """

    name: str

    def extract_pages(
        self, data: bytes, filename: str, pages: list[int]
    ) -> list[ExtractedPage]: ...


# Deliberately conservative. A missed heading costs a slightly worse `section`
# label on a citation; a false positive turns a sentence into a section title
# and drags unrelated chunks under it.
_MARKDOWN_HEADING = re.compile(r"^(#{1,4})\s+(\S.*?)\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+([A-Z][^.!?]{2,80})$")
_SHOUTED_HEADING = re.compile(r"^\s*([A-Z][A-Z0-9 \-/&',.]{3,70})\s*$")


def find_headings(text: str) -> list[str]:
    """Headings on a page, in the order they appear.

    Three shapes, because manufacturer manuals use all three and none of them
    consistently: Markdown hashes, decimal numbering (`4.2 Brine System`), and
    a line in capitals. Anything else is left to the chunker, which falls back
    to the last heading it saw.
    """
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > 90:
            continue

        markdown = _MARKDOWN_HEADING.match(stripped)
        if markdown:
            headings.append(markdown.group(2))
            continue

        numbered = _NUMBERED_HEADING.match(stripped)
        if numbered:
            headings.append(f"{numbered.group(1)} {numbered.group(2)}")
            continue

        # A shouted line is only a heading if it is short. Safety banners are
        # set in capitals too — "WARNING DISCONNECT POWER BEFORE SERVICING THIS
        # UNIT" — and promoting one to a section title drags every chunk after
        # it under the wrong heading. Six words is generous for a real heading
        # and short for a sentence.
        shouted = _SHOUTED_HEADING.match(stripped)
        if shouted and len(stripped.split()) <= 6:
            headings.append(shouted.group(1).title())

    return headings


def normalise(text: str) -> str:
    """Tidy extractor output without changing what it says.

    PDF text arrives with hyphenated line breaks, hard-wrapped paragraphs and
    runs of blank lines. Left alone, a chunk boundary lands mid-word and the
    embedding is of something nobody wrote.
    """
    # A hyphen at end of line followed by a lowercase letter is a split word.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Collapse single newlines inside a paragraph, keep the blank-line breaks.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
