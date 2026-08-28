"""Choosing an extractor.

Selection is by file type first and configuration second: a Markdown SOP goes to
the Markdown extractor whatever `PDF_EXTRACTOR` says, because that setting is
about which PDF library to use, not about what a `.md` file is.
"""

from __future__ import annotations

from app.config import Settings
from app.rag.extract.base import OcrExtractor, TextExtractor, find_headings, normalise
from app.rag.extract.markdown import MarkdownExtractor, PlainTextExtractor
from app.rag.extract.ocr import NullOcrExtractor, PaddleOcrExtractor, should_ocr
from app.rag.extract.pdf import PdfPlumberExtractor, PyMuPdfExtractor

__all__ = [
    "MarkdownExtractor",
    "NullOcrExtractor",
    "OcrExtractor",
    "PaddleOcrExtractor",
    "PdfPlumberExtractor",
    "PlainTextExtractor",
    "PyMuPdfExtractor",
    "TextExtractor",
    "UnsupportedDocumentError",
    "find_headings",
    "normalise",
    "ocr_extractor_for",
    "select_extractor",
    "should_ocr",
]


class UnsupportedDocumentError(Exception):
    """Raised before anything is written, so a bad upload leaves no half-row."""


def _pdf_extractor(settings: Settings) -> TextExtractor:
    return PyMuPdfExtractor() if settings.pdf_extractor == "pymupdf" else PdfPlumberExtractor()


def select_extractor(
    settings: Settings, filename: str, content_type: str | None = None
) -> TextExtractor:
    candidates: list[TextExtractor] = [
        _pdf_extractor(settings),
        MarkdownExtractor(),
        PlainTextExtractor(),
    ]
    for candidate in candidates:
        if candidate.supports(filename, content_type):
            return candidate

    raise UnsupportedDocumentError(
        f"{filename}: only PDF, Markdown and plain text can be ingested."
    )


def ocr_extractor_for(settings: Settings) -> OcrExtractor:
    if settings.ocr_provider == "paddle":
        return PaddleOcrExtractor()
    return NullOcrExtractor()
