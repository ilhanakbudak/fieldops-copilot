"""The ingestion pipeline.

    bytes → pages → chunks → vectors → rows

Four properties are worth more than the steps themselves:

**Idempotent.** A document is keyed by the SHA-256 of its bytes. Re-uploading
the same manual is a conflict, not a second copy — and a corpus with the same
manual in it twice does not merely waste space, it doubles that document's
weight in every result list.

**Re-runnable.** Re-indexing deletes the document's chunks and rebuilds them.
That is the operation you need after changing the chunk size, switching
embedding provider, or fixing an extractor, and it must not require deleting
and re-uploading the source.

**Honest about failure.** A page with no text layer and no OCR is counted and
reported on the document, not silently ingested as blank. "Ready, 412 chunks,
14 pages had no text" is a true status. "Ready" alone is not.

That is also why ingestion is deliberately **not one transaction.** The document
row is committed as `processing` before any work starts, and the outcome —
`ready` or `failed`, with the reason — is committed separately. Two reasons, and
both matter: a 400-page manual takes minutes to index, and holding a write
transaction open for that blocks every other writer; and if it *does* fail, the
row has to survive the caller's rollback, or the failure vanishes and the
operator is left guessing why their upload disappeared.

**Role tags follow the document.** `allowed_roles` is copied onto every chunk as
it is written, and re-tagging a document rewrites its chunks in the same
transaction. The retrieval query filters on the chunk copy, so the two must
never be allowed to disagree.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.rbac import Role
from app.config import Settings, get_settings
from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.ids import new_id
from app.db.models import Chunk, Document
from app.rag.chunk import chunk_document
from app.rag.embed import EmbeddingProvider, get_embedding_provider
from app.rag.extract import ocr_extractor_for, select_extractor, should_ocr
from app.rag.types import ExtractedDocument, ExtractedPage

logger = logging.getLogger("fieldops.rag.ingest")

# Embedding is CPU-bound and releases the GIL inside ONNX. Batching keeps the
# event loop responsive while a 400-page manual is being indexed.
EMBED_BATCH = 64


@dataclass(frozen=True, slots=True)
class IngestResult:
    document_id: str
    title: str
    pages: int
    chunks: int
    pages_without_text: list[int]
    ocr_pages: list[int]
    duration_ms: int


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(data: bytes, filename: str, settings: Settings) -> ExtractedDocument:
    """Pages of text, with OCR filling in only where it is needed.

    The per-page fallback is the whole reason this is not one call: OCR-ing a
    400-page manual to recover three scanned diagrams costs a hundred times what
    the diagrams are worth, and takes minutes instead of seconds.
    """
    extractor = select_extractor(settings, filename)
    pages = extractor.extract(data, filename)

    needs_ocr = [page.number for page in pages if should_ocr(page, settings.ocr_min_chars_per_page)]
    recovered: dict[int, ExtractedPage] = {}
    if needs_ocr:
        ocr = ocr_extractor_for(settings)
        try:
            recovered = {page.number: page for page in ocr.extract_pages(data, filename, needs_ocr)}
        except Exception:
            logger.exception("OCR failed for %s; keeping the text-layer result", filename)

    merged = [recovered.get(page.number, page) for page in pages]
    empty = [page.number for page in merged if page.is_empty]
    return ExtractedDocument(pages=merged, empty_pages=empty)


async def ingest_document(
    db: AsyncSession,
    *,
    data: bytes,
    filename: str,
    title: str,
    doc_type: str,
    allowed_roles: list[Role],
    uploaded_by: str | None = None,
    settings: Settings | None = None,
    embedder: EmbeddingProvider | None = None,
) -> IngestResult:
    settings = settings or get_settings()
    started = time.perf_counter()

    digest = content_hash(data)
    existing = (
        await db.execute(select(Document).where(Document.content_hash == digest))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"That file is already in the corpus as “{existing.title}”.",
            detail={"documentId": existing.id},
        )

    document = Document(
        id=new_id(),
        title=title.strip(),
        doc_type=doc_type.strip(),
        source_filename=filename,
        content_hash=digest,
        allowed_roles=allowed_roles,
        status="processing",
        uploaded_by=uploaded_by,
    )
    db.add(document)
    # Committed before indexing starts — see the module docstring. The row now
    # exists whatever happens next.
    await db.commit()

    try:
        result = await _index(db, document, data, settings, embedder)
        await db.commit()
    except Exception as error:
        await _mark_failed(db, document, error)
        await audit(
            "document.ingest",
            outcome="error",
            resource_type="document",
            resource_id=document.id,
            detail={"filename": filename, "error": str(error)[:300]},
        )
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    await audit(
        "document.ingest",
        resource_type="document",
        resource_id=document.id,
        detail={
            "filename": filename,
            "pages": result.pages,
            "chunks": result.chunks,
            "pagesWithoutText": len(result.pages_without_text),
            "durationMs": duration_ms,
        },
    )
    return IngestResult(
        document_id=document.id,
        title=document.title,
        pages=result.pages,
        chunks=result.chunks,
        pages_without_text=result.pages_without_text,
        ocr_pages=result.ocr_pages,
        duration_ms=duration_ms,
    )


async def reindex_document(
    db: AsyncSession,
    document_id: str,
    data: bytes,
    *,
    settings: Settings | None = None,
    embedder: EmbeddingProvider | None = None,
) -> IngestResult:
    """Rebuild one document's chunks from its source bytes.

    Needed after a chunk-size change, an extractor fix, or a switch of embedding
    provider — none of which should require deleting and re-uploading a manual
    somebody spent an afternoon collecting.
    """
    settings = settings or get_settings()
    started = time.perf_counter()

    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("No such document.")

    await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    document.status = "processing"
    document.error = None
    await db.commit()

    try:
        result = await _index(db, document, data, settings, embedder)
        await db.commit()
    except Exception as error:
        await _mark_failed(db, document, error)
        await audit(
            "document.reindex",
            outcome="error",
            resource_type="document",
            resource_id=document_id,
            detail={"error": str(error)[:300]},
        )
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)

    await audit(
        "document.reindex",
        resource_type="document",
        resource_id=document_id,
        detail={"chunks": result.chunks, "durationMs": duration_ms},
    )
    return IngestResult(
        document_id=document_id,
        title=document.title,
        pages=result.pages,
        chunks=result.chunks,
        pages_without_text=result.pages_without_text,
        ocr_pages=result.ocr_pages,
        duration_ms=duration_ms,
    )


async def retag_document(db: AsyncSession, document_id: str, roles: list[Role]) -> int:
    """Change who may read a document.

    Rewrites the copy on every chunk in the same transaction. The retrieval
    query filters on that copy, so a document and its chunks disagreeing about
    their audience is a data leak with a plausible-looking admin screen above
    it.
    """
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("No such document.")

    previous = [role.value for role in document.allowed_roles]
    document.allowed_roles = roles
    await db.execute(
        update(Chunk).where(Chunk.document_id == document_id).values(allowed_roles=roles)
    )

    count = int(
        (
            await db.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    )
    await audit(
        "document.retag",
        resource_type="document",
        resource_id=document_id,
        detail={"from": previous, "to": [role.value for role in roles], "chunks": count},
    )
    return count


async def delete_document(db: AsyncSession, document_id: str) -> None:
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("No such document.")

    title = document.title
    # Chunks go with it by ON DELETE CASCADE — enforced by the database, and by
    # the SQLite pragma set in app/db/engine.py, because a corpus that keeps
    # answering from a deleted manual is the worst kind of stale.
    await db.delete(document)
    await audit(
        "document.delete",
        resource_type="document",
        resource_id=document_id,
        detail={"title": title},
    )


async def _mark_failed(db: AsyncSession, document: Document, error: Exception) -> None:
    """Record why, on its own transaction.

    The caller is about to see an exception and roll back. Rolling the reason
    back with it would leave a document stuck in `processing` with nothing to
    explain it.
    """
    await db.rollback()
    await db.execute(
        update(Document)
        .where(Document.id == document.id)
        .values(status="failed", error=str(error)[:2000])
    )
    await db.commit()


@dataclass(frozen=True, slots=True)
class _IndexResult:
    pages: int
    chunks: int
    pages_without_text: list[int]
    ocr_pages: list[int]


async def _index(
    db: AsyncSession,
    document: Document,
    data: bytes,
    settings: Settings,
    embedder: EmbeddingProvider | None,
) -> _IndexResult:
    embedder = embedder or get_embedding_provider()

    # Extraction and chunking are synchronous and CPU-bound. Off the event loop,
    # or one large upload stalls every other request in the process.
    extracted = await asyncio.to_thread(extract, data, document.source_filename, settings)
    pieces = await asyncio.to_thread(chunk_document, extracted, settings)

    if not pieces:
        raise ValueError(
            "No text could be extracted. If this is a scanned document, enable OCR "
            "with OCR_PROVIDER=paddle."
        )

    vectors: list[list[float]] = []
    for start in range(0, len(pieces), EMBED_BATCH):
        batch = [piece.content for piece in pieces[start : start + EMBED_BATCH]]
        vectors.extend(await asyncio.to_thread(embedder.embed_documents, batch))

    roles = list(document.allowed_roles)
    db.add_all(
        Chunk(
            id=new_id(),
            document_id=document.id,
            ordinal=piece.ordinal,
            parent_index=piece.parent_index,
            content=piece.content,
            page=piece.page,
            section=piece.section,
            embedding=vector,
            # Copied down, not joined — see the module docstring.
            allowed_roles=roles,
        )
        for piece, vector in zip(pieces, vectors, strict=True)
    )

    document.page_count = extracted.page_count
    document.status = "ready"
    document.error = None
    document.ingested_at = utcnow()
    await db.flush()

    return _IndexResult(
        pages=extracted.page_count,
        chunks=len(pieces),
        pages_without_text=extracted.empty_pages,
        ocr_pages=[page.number for page in extracted.pages if page.source == "paddle"],
    )
