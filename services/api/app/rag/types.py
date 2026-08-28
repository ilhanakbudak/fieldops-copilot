"""What flows through the ingestion pipeline.

Plain frozen dataclasses rather than ORM rows: extraction and chunking are pure
functions over text, and keeping a database session out of them is what makes
them testable without one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One page of a source document.

    `number` is 1-indexed, as a reader counts, because it ends up in a citation
    that somebody has to be able to act on.
    """

    number: int
    text: str
    # Which extractor produced it: "pdfplumber", "pymupdf", "paddle", "markdown".
    # Recorded per page rather than per document because a scanned insert in an
    # otherwise digital manual is normal, and "which pages needed OCR" is the
    # first question when an answer comes back wrong.
    source: str
    # Headings found on this page, largest first. Used to attribute a section to
    # each chunk without a second pass over the document.
    headings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    pages: list[ExtractedPage]
    # Pages that produced no usable text and had no OCR to fall through to.
    # Surfaced rather than swallowed: a manual that ingested "successfully" with
    # forty blank pages is worse than one that failed.
    empty_pages: list[int] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if not page.is_empty)


@dataclass(frozen=True, slots=True)
class TextChunk:
    """A retrievable passage, before it reaches the database.

    `parent_index` groups the small chunks that belong to one larger section.
    Retrieval matches on these; the prompt receives the parent. That split is
    the difference between a citation that is technically correct and an answer
    that is actually useful.
    """

    ordinal: int
    content: str
    page: int | None
    section: str | None
    parent_index: int


@dataclass(frozen=True, slots=True)
class ParentSection:
    """The wider context a matched chunk expands into."""

    index: int
    content: str
    page: int | None
    section: str | None
