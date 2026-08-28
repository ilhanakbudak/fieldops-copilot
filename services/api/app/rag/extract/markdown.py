"""Markdown and plain text.

Not every internal document was ever a PDF. SOPs, FAQs and training notes are
often Markdown or a pasted wiki page, and round-tripping those through a PDF to
extract them again would lose the structure this pipeline is trying to keep.

It is also how the synthetic corpus is stored: Markdown is diffable in review,
which a binary PDF is not.
"""

from __future__ import annotations

import re

from app.rag.extract.base import find_headings, normalise
from app.rag.types import ExtractedPage

# Markdown has no pages. Splitting on a horizontal rule gives a document author
# an explicit way to say "this is a page", and falling back to one page for the
# whole file is honest — a citation then reads "no page", not a made-up number.
_PAGE_BREAK = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$", re.MULTILINE)


class MarkdownExtractor:
    name = "markdown"

    def supports(self, filename: str, content_type: str | None) -> bool:
        lowered = filename.lower()
        return lowered.endswith((".md", ".markdown", ".txt")) or (
            content_type in {"text/markdown", "text/plain"}
        )

    def extract(self, data: bytes, filename: str) -> list[ExtractedPage]:
        text = data.decode("utf-8", errors="replace")
        parts = [part for part in _PAGE_BREAK.split(text) if part.strip()] or [text]

        return [
            ExtractedPage(
                number=index,
                # Markdown is already laid out the way its author meant it, so
                # only whitespace is normalised — unwrapping its lines the way
                # PDF text needs would destroy lists and code blocks.
                text=_tidy(part),
                source=self.name,
                headings=find_headings(part),
            )
            for index, part in enumerate(parts, start=1)
        ]


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class PlainTextExtractor(MarkdownExtractor):
    """Same handling, different name in the audit trail.

    Kept distinct so `page.source` records what was actually ingested — and so
    that if plain text ever needs different treatment, there is a class to
    change rather than a branch to add.
    """

    name = "text"

    def extract(self, data: bytes, filename: str) -> list[ExtractedPage]:
        pages = super().extract(data, filename)
        return [
            ExtractedPage(
                number=page.number,
                text=normalise(page.text),
                source=self.name,
                headings=page.headings,
            )
            for page in pages
        ]
