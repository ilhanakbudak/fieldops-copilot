"""OCR for pages with no text layer.

**PaddleOCR (PP-OCRv5)** is the default choice, and the reasoning is worth
recording because the obvious answer is now the wrong one.

The current benchmark leader is PaddleOCR-VL, which tops OmniDocBench — and is
GPU-only, with no CPU or ARM build. Making it the default would mean this
repository no longer runs on the laptop of the person reviewing it, in exchange
for accuracy on scanned pages a synthetic corpus does not have. The classical
PP-OCRv5 pipeline is Apache-2.0, runs on CPU, and is comfortably the strongest
of the engines that do. Surya and Docling are the credible alternatives; Surya
is the one worth benchmarking against if scan quality turns out to be poor.

So: PP-OCRv5 on CPU by default, PaddleOCR-VL behind the same protocol for
deployments that have a GPU. Both are an optional extra, because a deep-learning
runtime is a heavy thing to install for a corpus that may contain no scans at
all.

The part that matters more than the engine is *when* it runs — see
`should_ocr`. Running OCR over every page of a 400-page manual costs a hundred
times what recovering three scanned diagrams is worth.
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.extract.base import find_headings, normalise
from app.rag.types import ExtractedPage

logger = logging.getLogger("fieldops.rag.ocr")


def should_ocr(page: ExtractedPage, min_chars: int) -> bool:
    """Whether this page's text layer is missing or too thin to trust.

    Not `len(text) == 0`: a scanned page in a PDF usually still carries a
    running header, a page number and a stamped revision code, which together
    clear zero without carrying any of the content.
    """
    return len(page.text.strip()) < min_chars


class NullOcrExtractor:
    """The default: no OCR configured.

    Returns nothing, and the pipeline records those pages as empty. A document
    that ingested "successfully" with forty blank pages is worse than one that
    failed, so the count is surfaced on the document rather than swallowed.
    """

    name = "none"

    def extract_pages(self, data: bytes, filename: str, pages: list[int]) -> list[ExtractedPage]:
        if pages:
            logger.info(
                "%d page(s) of %s have no text layer and OCR is disabled", len(pages), filename
            )
        return []


class PaddleOcrExtractor:
    """PP-OCRv5 on CPU. Imported lazily so the extra stays optional."""

    name = "paddle"

    def __init__(self, language: str = "en") -> None:
        self._language = language
        self._engine: object | None = None

    def _get_engine(self) -> object:
        if self._engine is None:  # pragma: no cover - requires the optional extra
            try:
                from paddleocr import PaddleOCR
            except ImportError as error:
                raise RuntimeError(
                    "OCR_PROVIDER=paddle requires the optional extra: `uv sync --extra ocr`."
                ) from error

            # Angle classification is worth its cost here: scanned inserts in
            # service manuals are frequently rotated, and a sideways page OCRs
            # into nothing rather than into something obviously wrong.
            self._engine = PaddleOCR(use_angle_cls=True, lang=self._language)
        return self._engine

    def extract_pages(
        self, data: bytes, filename: str, pages: list[int]
    ) -> list[ExtractedPage]:  # pragma: no cover - requires the optional extra
        engine = self._get_engine()
        rendered = _render_pages(data, pages)

        extracted: list[ExtractedPage] = []
        for number, image in rendered:
            result = engine.ocr(image)  # type: ignore[attr-defined]
            text = normalise("\n".join(_lines(result)))
            extracted.append(
                ExtractedPage(
                    number=number, text=text, source=self.name, headings=find_headings(text)
                )
            )
        return extracted


def _render_pages(data: bytes, pages: list[int]) -> list[tuple[int, bytes]]:  # pragma: no cover
    """Rasterise only the pages that need it.

    pypdfium2 arrives with pdfplumber, so rendering costs no extra dependency.
    200 DPI is the usual floor for reliable OCR of body text; below it, digits
    in a part number start to swap.
    """
    import pypdfium2

    document = pypdfium2.PdfDocument(data)
    rendered: list[tuple[int, bytes]] = []
    try:
        for number in pages:
            bitmap = document[number - 1].render(scale=200 / 72)
            buffer = bitmap.to_pil()
            import io

            out = io.BytesIO()
            buffer.save(out, format="PNG")
            rendered.append((number, out.getvalue()))
    finally:
        document.close()
    return rendered


def _lines(result: Any) -> list[str]:  # pragma: no cover - requires the optional extra
    """Flatten PaddleOCR's nested result into lines.

    Its output shape has changed across major versions, so this reads
    defensively rather than indexing into a structure that may have moved.
    """
    lines: list[str] = []
    for page in result or []:
        for entry in page or []:
            if isinstance(entry, list | tuple) and len(entry) >= 2:
                payload = entry[1]
                if isinstance(payload, list | tuple) and payload:
                    lines.append(str(payload[0]))
                elif isinstance(payload, str):
                    lines.append(payload)
    return lines
