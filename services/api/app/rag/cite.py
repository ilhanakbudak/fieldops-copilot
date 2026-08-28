"""Citations, resolved rather than requested.

The requirement is that answers show the document they came from. The obvious
implementation — ask the model to name its sources — produces citations that
look right and are not: a plausible document title, a page number that reads
like a page number, both invented. There is no way for a reader to tell, which
makes it worse than no citation at all.

So the model is never asked to *name* anything. Each retrieved passage is
rendered under an opaque marker, the model is constrained to emit those markers,
and the API resolves them against the passages it actually retrieved, before the
response leaves the server:

    prompt      [S3] NG-4200 Water Softener — Service Manual · page 3
    model       "…break the salt bridge with a broom handle [S3]."
    resolved    S3 → document 8f2c…, page 3, "E-04 Brine Valve Fault"

A marker the model invented — `[S9]` when six passages were retrieved — resolves
to nothing and is **stripped from the text**, not displayed. The sentence stays;
the false attribution does not. That is the whole reason this is a server-side
resolution step rather than a rendering concern in the browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.search.pipeline import Passage

# Tolerates the shapes a model actually produces: [S1], [S1, S2], [S1][S2].
_MARKER = re.compile(r"\[\s*([Ss]\d+(?:\s*,\s*[Ss]\d+)*)\s*\]")
_SINGLE = re.compile(r"[Ss](\d+)")


@dataclass(frozen=True, slots=True)
class Citation:
    """Enough provenance for a reader to go and check."""

    marker: str
    chunk_id: str
    document_id: str
    document_title: str
    page: int | None
    section: str | None
    snippet: str


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    text: str
    citations: list[Citation]
    # Markers the model produced that matched no retrieved passage. Kept rather
    # than discarded silently: a model that invents citations is worth knowing
    # about, and this is the only place that fact exists.
    dropped: list[str]


def render_sources(passages: list[Passage]) -> str:
    """The passages, as the prompt presents them.

    Title, section and page go in the header so the model can refer to them in
    prose — "the service manual says" reads better than "[S3] says" — while the
    marker remains the only thing it is allowed to cite.
    """
    blocks: list[str] = []
    for passage in passages:
        location = " · ".join(
            part
            for part in (
                passage.document_title,
                passage.section,
                f"page {passage.page}" if passage.page else None,
            )
            if part
        )
        blocks.append(f"[{passage.marker}] {location}\n{passage.content}")
    return "\n\n".join(blocks)


def resolve(text: str, passages: list[Passage], *, snippet_chars: int = 320) -> ResolvedAnswer:
    by_marker = {passage.marker.upper(): passage for passage in passages}
    cited: dict[str, Citation] = {}
    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        markers = [f"S{number}" for number in _SINGLE.findall(match.group(1))]
        kept: list[str] = []

        for marker in markers:
            passage = by_marker.get(marker)
            if passage is None:
                dropped.append(marker)
                continue
            kept.append(marker)
            if marker not in cited:
                cited[marker] = Citation(
                    marker=marker,
                    chunk_id=passage.chunk_id,
                    document_id=passage.document_id,
                    document_title=passage.document_title,
                    page=passage.page,
                    section=passage.section,
                    snippet=_snippet(passage.content, snippet_chars),
                )

        # Every marker in the group was invented: remove the brackets entirely
        # rather than leaving an empty pair behind.
        return "".join(f"[{marker}]" for marker in kept)

    resolved = _MARKER.sub(replace, text)
    # Substitution can leave a doubled space where a marker was removed.
    resolved = re.sub(r"[ \t]{2,}", " ", resolved)
    resolved = re.sub(r"\s+([.,;:])", r"\1", resolved).strip()

    ordered = [cited[marker] for marker in sorted(cited, key=lambda m: int(m[1:]))]
    return ResolvedAnswer(text=resolved, citations=ordered, dropped=dropped)


def _snippet(content: str, limit: int) -> str:
    text = " ".join(content.split())
    if len(text) <= limit:
        return text
    # Cut at a word boundary; a snippet ending mid-word reads as a bug.
    return text[:limit].rsplit(" ", 1)[0] + "…"
