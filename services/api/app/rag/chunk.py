"""Splitting a document into what retrieval searches.

Three ideas do the work here, and each exists because the naive version produces
answers that are technically sourced and practically useless.

**Split on structure, not on a character count.** A fixed-width window cuts
through the middle of a troubleshooting table and embeds half a symptom with
half an unrelated remedy. Paragraph and heading boundaries come first; the
character budget only decides when to stop accumulating.

**But structure alone is not enough either.** Splitting on blank lines leaves a
heading as a block of its own, and emitting that as a chunk produces a passage
that embeds beautifully against a query about the heading and contains no
answer. So consecutive blocks are merged up to the budget, and a heading is
merged *forward* into the text it introduces.

**Small chunks for matching, large sections for reading.** A 900-character chunk
embeds precisely — it is about one thing, so its vector means one thing. Handing
that to the model loses the sentence before it that said which valve was under
discussion. Every chunk therefore records the parent section it came from;
retrieval matches the chunk and the prompt receives the parent.

The parent is not stored twice. `parent_index` groups chunks and the section is
reassembled from its members at query time, which also means re-tuning the
parent size later does not require re-ingesting the corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import Settings
from app.rag.types import ExtractedDocument, ParentSection, TextChunk

# A blank line is the most reliable paragraph signal that survives PDF
# extraction; a sentence end followed by a capital is the fallback for pages
# that arrive as one long run.
_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


@dataclass(frozen=True, slots=True)
class _Block:
    text: str
    page: int
    section: str | None
    starts_section: bool


def chunk_document(document: ExtractedDocument, settings: Settings) -> list[TextChunk]:
    units = _merge(_collect(document), settings.chunk_chars)

    chunks: list[TextChunk] = []
    ordinal = 0
    parent_index = 0
    parent_chars = 0

    for unit in units:
        # A heading starts a new parent section, and so does exceeding the
        # parent budget. Without the first rule a section boundary can fall
        # inside a parent and the model reads two topics as one passage.
        if parent_chars and (
            unit.starts_section or parent_chars + len(unit.text) > settings.parent_chars
        ):
            parent_index += 1
            parent_chars = 0

        for piece in _split(unit.text, settings.chunk_chars, settings.chunk_overlap_chars):
            chunks.append(
                TextChunk(
                    ordinal=ordinal,
                    content=piece,
                    page=unit.page,
                    section=unit.section,
                    parent_index=parent_index,
                )
            )
            ordinal += 1

        parent_chars += len(unit.text)

    return chunks


def parent_sections(chunks: list[TextChunk]) -> dict[int, ParentSection]:
    """Reassemble each parent from its chunks.

    Overlap means consecutive chunks repeat their join, so pieces are stitched
    on their longest common boundary rather than concatenated — otherwise the
    model reads the same sentence twice and treats the repetition as emphasis.
    """
    grouped: dict[int, list[TextChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.parent_index, []).append(chunk)

    sections: dict[int, ParentSection] = {}
    for index, members in grouped.items():
        members.sort(key=lambda chunk: chunk.ordinal)
        content = members[0].content
        for chunk in members[1:]:
            content = _stitch(content, chunk.content)
        sections[index] = ParentSection(
            index=index,
            content=content,
            page=members[0].page,
            section=members[0].section,
        )
    return sections


def _collect(document: ExtractedDocument) -> list[_Block]:
    """Paragraph-sized blocks across the whole document, each tagged with the
    section it sits under.

    The section is threaded across pages rather than reset per page: a section
    that runs onto the next page is still that section, and a chunk starting
    halfway down a page inherits the last heading seen.
    """
    blocks: list[_Block] = []
    section: str | None = None

    for page in document.pages:
        if page.is_empty:
            continue

        headings = set(page.headings)
        for raw in _PARAGRAPH.split(page.text):
            text = raw.strip()
            if not text:
                continue

            first_line = text.splitlines()[0].strip().lstrip("# ").strip()
            starts = first_line in headings
            if starts:
                section = first_line

            blocks.append(
                _Block(text=text, page=page.number, section=section, starts_section=starts)
            )

    return blocks


def _merge(blocks: list[_Block], size: int) -> list[_Block]:
    """Combine consecutive blocks up to the chunk budget.

    Four rules, in order:

    1. A unit that is *only* a heading absorbs whatever comes next, whatever it
       is. This is the nested-heading case — "## Error Codes" immediately
       followed by "### E-04 Brine Valve Fault" — where rule 2 alone would
       strand the outer heading as a chunk of three words. The unit takes the
       more specific section, because that is what a citation should name.
    2. A block that starts a section begins a new unit, so a heading is never
       stranded at the end of the previous topic.
    3. Otherwise a block joins the current unit while it fits.
    4. A block that does not fit begins a new unit.

    A unit inherits the page of its first block, which is what a citation should
    point at: the place a reader would start reading.
    """
    units: list[_Block] = []
    current: _Block | None = None

    for block in blocks:
        if current is None:
            current = block
            continue

        if _is_bare_heading(current):
            current = _Block(
                text=f"{current.text}\n\n{block.text}",
                page=current.page,
                section=block.section,
                starts_section=True,
            )
            continue

        if block.starts_section or len(current.text) + len(block.text) + 2 > size:
            units.append(current)
            current = block
            continue

        current = _Block(
            text=f"{current.text}\n\n{block.text}",
            page=current.page,
            section=current.section,
            starts_section=current.starts_section,
        )

    if current is not None:
        units.append(current)

    return units


def _is_bare_heading(block: _Block) -> bool:
    """A heading with nothing under it yet."""
    return block.starts_section and len(block.text.strip().splitlines()) == 1


def _split(block: str, size: int, overlap: int) -> list[str]:
    """Break one unit into chunks, preferring sentence boundaries."""
    if len(block) <= size:
        return [block]

    sentences = _SENTENCE.split(block)
    pieces: list[str] = []
    current = ""

    for sentence in sentences:
        # A single sentence longer than the budget — a table row, a long
        # parameter list — is taken whole rather than cut mid-clause. One
        # oversized chunk is a better failure than two meaningless ones.
        if len(sentence) > size:
            if current:
                pieces.append(current.strip())
                current = ""
            pieces.append(sentence.strip())
            continue

        if len(current) + len(sentence) + 1 > size:
            pieces.append(current.strip())
            current = _tail(current, overlap)

        current = f"{current} {sentence}".strip() if current else sentence

    if current.strip():
        pieces.append(current.strip())

    return [piece for piece in pieces if piece]


def _tail(text: str, overlap: int) -> str:
    """The trailing context carried into the next chunk.

    Overlap exists so a fact split across a boundary is still retrievable from
    either side. Cut on a sentence start rather than a character count, or the
    carried fragment begins mid-word and pollutes the embedding.
    """
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    match = _SENTENCE.search(tail)
    return tail[match.end() :] if match else tail


def _stitch(left: str, right: str) -> str:
    """Join two overlapping chunks without repeating the shared part."""
    limit = min(len(left), len(right))
    for size in range(limit, 20, -1):
        if left.endswith(right[:size]):
            return left + right[size:]
    return f"{left} {right}"
