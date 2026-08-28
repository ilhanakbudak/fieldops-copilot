"""PDF text extraction.

Two implementations of the same protocol, and the choice between them is a
licence decision as much as a technical one:

**pdfplumber** (MIT, on pdfminer.six) is the default. It is not the fastest
thing available, and it is the fastest thing this repository can depend on
without changing its own licence.

**PyMuPDF** is several times quicker and is AGPL-3.0. An MIT repository cannot
take that as a hard dependency without the obligation travelling to anyone who
uses it, so it lives behind an optional extra and a lazy import. A deployment
that has a commercial PyMuPDF licence — or that is happy with AGPL — installs
`.[pymupdf]` and sets `PDF_EXTRACTOR=pymupdf`. Nothing else changes.

That is the whole argument for the protocol: the pipeline should not know or
care, and swapping the two must not be a rewrite.
"""

from __future__ import annotations

import io
import logging

from app.rag.extract.base import find_headings, normalise
from app.rag.types import ExtractedPage

logger = logging.getLogger("fieldops.rag.extract")

_PDF_SUFFIXES = (".pdf",)


def _supports(filename: str, content_type: str | None) -> bool:
    return filename.lower().endswith(_PDF_SUFFIXES) or content_type == "application/pdf"


class PdfPlumberExtractor:
    """The default. MIT, no native build step, honest about layout."""

    name = "pdfplumber"

    def supports(self, filename: str, content_type: str | None) -> bool:
        return _supports(filename, content_type)

    def extract(self, data: bytes, filename: str) -> list[ExtractedPage]:
        import pdfplumber

        pages: list[ExtractedPage] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                # `layout=False` keeps columns from being interleaved into each
                # other. Manuals are frequently two-column, and interleaved text
                # embeds as noise — worse than no text at all, because it looks
                # like a successful extraction.
                raw = page.extract_text(layout=False) or ""
                pages.append(
                    ExtractedPage(
                        number=number,
                        text=normalise(raw),
                        source=self.name,
                        headings=find_headings(raw),
                    )
                )
        return pages


class PyMuPdfExtractor:
    """Faster, AGPL-3.0, opt-in. Imported lazily so it is genuinely optional."""

    name = "pymupdf"

    def supports(self, filename: str, content_type: str | None) -> bool:
        return _supports(filename, content_type)

    def extract(self, data: bytes, filename: str) -> list[ExtractedPage]:
        try:
            import pymupdf
        except ImportError as error:  # pragma: no cover - depends on an extra
            raise RuntimeError(
                "PDF_EXTRACTOR=pymupdf requires the optional extra: "
                "`uv sync --extra pymupdf`. Note that PyMuPDF is AGPL-3.0."
            ) from error

        pages: list[ExtractedPage] = []
        with pymupdf.open(stream=data, filetype="pdf") as document:
            for number, page in enumerate(document, start=1):
                # "text" mode preserves reading order per block, which is the
                # closest equivalent to pdfplumber's non-layout output — so
                # switching extractor changes the speed, not the chunk
                # boundaries.
                raw = page.get_text("text") or ""
                pages.append(
                    ExtractedPage(
                        number=number,
                        text=normalise(raw),
                        source=self.name,
                        headings=find_headings(raw),
                    )
                )
        return pages
