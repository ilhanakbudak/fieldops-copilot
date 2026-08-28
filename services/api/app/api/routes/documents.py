"""The knowledge base: upload, list, re-index, re-tag, delete — and a raw search.

The search endpoint here is deliberately *not* the chat endpoint. It returns
ranked passages with their scores and no model in the loop, which makes it the
thing to look at when an answer is wrong: it separates "retrieval found the
wrong passage" from "retrieval was fine and generation ignored it". Those have
completely different fixes, and without this endpoint they look identical.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbDep, PrincipalDep, SettingsDep, require
from app.api.schemas import (
    DocumentSummary,
    IngestResponse,
    RetagRequest,
    SearchHitOut,
    SearchResponse,
)
from app.audit import audit
from app.auth.rbac import Permission, Role
from app.core.errors import ApiError, NotFoundError
from app.db.models import Chunk, Document
from app.rag.embed import get_embedding_provider
from app.rag.extract import UnsupportedDocumentError
from app.rag.ingest import delete_document, ingest_document, reindex_document, retag_document
from app.rag.store import vector_store_for

router = APIRouter(prefix="/documents", tags=["documents"])

manage = [require(Permission.DOCUMENTS_MANAGE)]


async def _summarise(db: DbDep, document: Document) -> DocumentSummary:
    chunks = int(
        (
            await db.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
            )
        ).scalar_one()
    )
    return DocumentSummary(
        id=document.id,
        title=document.title,
        doc_type=document.doc_type,
        source_filename=document.source_filename,
        page_count=document.page_count,
        chunk_count=chunks,
        allowed_roles=list(document.allowed_roles),
        status=document.status,
        error=document.error,
        ingested_at=document.ingested_at,
        created_at=document.created_at,
    )


@router.get("", response_model=list[DocumentSummary])
async def list_documents(db: DbDep, principal: PrincipalDep) -> list[DocumentSummary]:
    """Everything the caller may read.

    An administrator sees the whole corpus; everyone else sees their own
    audience. Same rule as retrieval, applied in the query rather than by
    trimming the list afterwards.
    """
    query = (
        select(Document, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
        .options(selectinload(Document.chunks).load_only(Chunk.id))
    )

    rows = (await db.execute(query)).all()
    visible = [
        (document, count)
        for document, count in rows
        if principal.document_roles & set(document.allowed_roles)
    ]

    return [
        DocumentSummary(
            id=document.id,
            title=document.title,
            doc_type=document.doc_type,
            source_filename=document.source_filename,
            page_count=document.page_count,
            chunk_count=int(count),
            allowed_roles=list(document.allowed_roles),
            status=document.status,
            error=document.error,
            ingested_at=document.ingested_at,
            created_at=document.created_at,
        )
        for document, count in visible
    ]


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=manage,
)
async def upload_document(
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    doc_type: Annotated[str, Form()],
    # Repeated form field: `allowed_roles=technician&allowed_roles=office`.
    allowed_roles: Annotated[list[Role], Form()],
) -> IngestResponse:
    data = await file.read()
    if not data:
        raise ApiError("That file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise ApiError(
            f"Files are limited to {settings.max_upload_bytes // (1024 * 1024)} MB.",
            detail={"bytes": len(data)},
        )

    try:
        result = await ingest_document(
            db,
            data=data,
            filename=file.filename or "document",
            title=title,
            doc_type=doc_type,
            allowed_roles=allowed_roles,
            uploaded_by=principal.user_id,
            settings=settings,
        )
    except UnsupportedDocumentError as error:
        raise ApiError(str(error)) from error
    except ValueError as error:
        raise ApiError(str(error)) from error

    document = (
        await db.execute(select(Document).where(Document.id == result.document_id))
    ).scalar_one()

    return IngestResponse(
        document=await _summarise(db, document),
        chunks=result.chunks,
        pages=result.pages,
        pages_without_text=result.pages_without_text,
        duration_ms=result.duration_ms,
    )


@router.post("/{document_id}/reindex", response_model=IngestResponse, dependencies=manage)
async def reindex(
    document_id: str,
    db: DbDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> IngestResponse:
    """Rebuild a document's chunks from its source.

    The file is re-supplied rather than read back from storage, because object
    storage lands with the deployment milestone and pretending otherwise would
    mean an endpoint that only works in one environment.
    """
    data = await file.read()
    result = await reindex_document(db, document_id, data, settings=settings)

    document = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
    return IngestResponse(
        document=await _summarise(db, document),
        chunks=result.chunks,
        pages=result.pages,
        pages_without_text=result.pages_without_text,
        duration_ms=result.duration_ms,
    )


@router.patch("/{document_id}/roles", response_model=DocumentSummary, dependencies=manage)
async def retag(document_id: str, body: RetagRequest, db: DbDep) -> DocumentSummary:
    await retag_document(db, document_id, body.allowed_roles)
    document = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
    return await _summarise(db, document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=manage)
async def remove(document_id: str, db: DbDep) -> None:
    await delete_document(db, document_id)


@router.get("/search", response_model=SearchResponse)
async def search(
    db: DbDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    doc_type: Annotated[str | None, Query()] = None,
) -> SearchResponse:
    embedder = get_embedding_provider()
    vector = embedder.embed_query(q)

    store = vector_store_for(db, settings)
    hits = await store.search(
        vector,
        # Not optional, and not defaulted. The store's protocol has no overload
        # without it.
        roles=principal.document_roles,
        limit=limit,
        doc_types=[doc_type] if doc_type else None,
    )

    await audit(
        "rag.search",
        resource_type="query",
        detail={"query": q[:200], "hits": len(hits), "store": store.name},
    )

    return SearchResponse(
        hits=[
            SearchHitOut(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_title=hit.document_title,
                content=hit.content,
                page=hit.page,
                section=hit.section,
                score=hit.score,
            )
            for hit in hits
        ],
        searched_roles=sorted(principal.document_roles),
    )


@router.get("/{document_id}", response_model=DocumentSummary)
async def get_document(document_id: str, db: DbDep, principal: PrincipalDep) -> DocumentSummary:
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    # Same 404 whether it does not exist or the caller may not see it. A
    # distinguishable 403 confirms that a document with that id exists, which is
    # itself information about the corpus.
    if document is None or not (principal.document_roles & set(document.allowed_roles)):
        raise NotFoundError("No such document.")
    return await _summarise(db, document)
