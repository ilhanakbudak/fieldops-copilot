"""The pipeline, the corpus, and the role filter that runs inside the search."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role
from app.config import get_settings
from app.db.models import Chunk, Document
from app.rag.corpus import load_fixtures, seed_corpus
from app.rag.embed import get_embedding_provider
from app.rag.ingest import (
    delete_document,
    ingest_document,
    reindex_document,
    retag_document,
)
from app.rag.store import vector_store_for
from tests.conftest import login

MANUAL = b"""# NG-4200 Service Manual

## E-04 Brine Valve Fault

The brine draw cycle completed without the expected drop in brine tank level.
Check for a salt bridge in the cabinet before replacing the valve.

---

## E-14 Reserve Capacity Exceeded

Distinct from E-04 despite the similar code. The unit consumed its calculated
reserve before the scheduled regeneration.
"""


async def _ingest(
    db: AsyncSession,
    roles: list[Role],
    data: bytes = MANUAL,
    title: str = "NG-4200 Service Manual",
    doc_type: str = "manual",
) -> str:
    result = await ingest_document(
        db,
        data=data,
        filename=f"{title.lower().replace(' ', '-')}.md",
        title=title,
        doc_type=doc_type,
        allowed_roles=roles,
        settings=get_settings(),
    )
    return result.document_id


async def test_ingesting_writes_chunks_with_provenance(db: AsyncSession) -> None:
    document_id = await _ingest(db, [Role.TECHNICIAN])

    chunks = list(
        (await db.execute(select(Chunk).where(Chunk.document_id == document_id))).scalars()
    )

    assert chunks
    assert all(chunk.embedding is not None for chunk in chunks)
    assert all(chunk.page in {1, 2} for chunk in chunks)
    assert any(chunk.section == "E-04 Brine Valve Fault" for chunk in chunks)


async def test_role_tags_are_copied_onto_every_chunk(db: AsyncSession) -> None:
    """The retrieval query filters on the chunk copy. A document and its chunks
    disagreeing about their audience is a leak with a plausible admin screen
    above it."""
    document_id = await _ingest(db, [Role.SALES])

    chunks = list(
        (await db.execute(select(Chunk).where(Chunk.document_id == document_id))).scalars()
    )

    assert all(chunk.allowed_roles == [Role.SALES] for chunk in chunks)


async def test_the_same_file_cannot_be_ingested_twice(db: AsyncSession) -> None:
    """A corpus holding the same manual twice does not merely waste space — it
    doubles that document's weight in every result list."""
    from app.core.errors import ConflictError

    await _ingest(db, [Role.TECHNICIAN])

    try:
        await _ingest(db, [Role.TECHNICIAN])
    except ConflictError as error:
        assert "already in the corpus" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected a conflict on the content hash")


async def test_a_document_with_no_extractable_text_fails_loudly(db: AsyncSession) -> None:
    from app.core.errors import ApiError

    try:
        await _ingest(db, [Role.TECHNICIAN], data=b"   \n\n  ")
    except (ApiError, ValueError):
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ingestion to refuse an empty document")

    failed = (
        await db.execute(select(Document).where(Document.status == "failed"))
    ).scalar_one_or_none()
    # The row survives with the reason attached, so the failure is visible in
    # the admin list rather than vanishing.
    assert failed is not None
    assert failed.error


async def test_reindexing_replaces_chunks_rather_than_adding_to_them(
    db: AsyncSession,
) -> None:
    document_id = await _ingest(db, [Role.TECHNICIAN])
    before = int(
        (
            await db.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    )

    await reindex_document(db, document_id, MANUAL, settings=get_settings())

    after = int(
        (
            await db.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    )
    assert after == before


async def test_retagging_rewrites_the_chunks_in_the_same_transaction(
    db: AsyncSession,
) -> None:
    document_id = await _ingest(db, [Role.TECHNICIAN])

    await retag_document(db, document_id, [Role.SALES, Role.OFFICE])
    await db.commit()

    chunks = list(
        (await db.execute(select(Chunk).where(Chunk.document_id == document_id))).scalars()
    )
    assert all(set(chunk.allowed_roles) == {Role.SALES, Role.OFFICE} for chunk in chunks)


async def test_deleting_a_document_takes_its_chunks_with_it(db: AsyncSession) -> None:
    """A corpus that keeps answering from a deleted manual is the worst kind of
    stale."""
    document_id = await _ingest(db, [Role.TECHNICIAN])

    await delete_document(db, document_id)
    await db.commit()

    remaining = int(
        (
            await db.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    )
    assert remaining == 0


async def test_search_never_returns_a_chunk_the_caller_may_not_read(
    db: AsyncSession,
) -> None:
    """The point of AD-6, asserted directly on the store: a restricted chunk
    must not be in the candidate set, not merely absent from what is rendered."""
    await _ingest(
        db,
        [Role.SALES],
        data=b"# Dealer Pricing\n\nRadon system dealer cost is 2400.\n",
        title="Dealer Pricing",
        doc_type="pricing",
    )
    await _ingest(db, [Role.TECHNICIAN])

    store = vector_store_for(db, get_settings())
    vector = get_embedding_provider().embed_query("radon system dealer cost")

    technician_hits = await store.search(vector, roles=frozenset({Role.TECHNICIAN}), limit=20)
    sales_hits = await store.search(vector, roles=frozenset({Role.SALES}), limit=20)

    assert all("dealer cost" not in hit.content.lower() for hit in technician_hits)
    assert any("dealer cost" in hit.content.lower() for hit in sales_hits)


async def test_an_admin_searches_every_audience(db: AsyncSession) -> None:
    await _ingest(
        db,
        [Role.SALES],
        data=b"# Dealer Pricing\n\nRadon system dealer cost is 2400.\n",
        title="Dealer Pricing",
        doc_type="pricing",
    )
    await _ingest(db, [Role.TECHNICIAN])

    store = vector_store_for(db, get_settings())
    vector = get_embedding_provider().embed_query("radon system dealer cost")

    hits = await store.search(vector, roles=frozenset(Role), limit=20)

    assert {hit.document_title for hit in hits} == {"NG-4200 Service Manual", "Dealer Pricing"}


async def test_the_fixture_corpus_declares_an_audience_for_every_document() -> None:
    """The access-control demo comes from this front matter. A document without
    it would be invisible to everyone, which looks like a broken index."""
    fixtures = load_fixtures()

    assert len(fixtures) >= 6
    assert all(fixture.allowed_roles for fixture in fixtures)
    assert any(fixture.allowed_roles == [Role.SALES] for fixture in fixtures)


async def test_seeding_the_corpus_twice_does_not_duplicate_it(db: AsyncSession) -> None:
    first = await seed_corpus(db, settings=get_settings())
    second = await seed_corpus(db, settings=get_settings())

    assert first >= 6
    assert second == 0


class TestOverHttp:
    """The same rules, through the API a browser actually calls."""

    async def test_a_technician_cannot_upload(self, client: AsyncClient) -> None:
        await login(client, "technician")

        response = await client.post(
            "/documents",
            files={"file": ("sop.md", MANUAL, "text/markdown")},
            data={"title": "Manual", "doc_type": "manual", "allowed_roles": ["technician"]},
        )

        assert response.status_code == 403

    async def test_an_admin_can_upload_and_then_search_it(self, client: AsyncClient) -> None:
        await login(client, "admin")

        upload = await client.post(
            "/documents",
            files={"file": ("manual.md", MANUAL, "text/markdown")},
            data={"title": "NG-4200 Manual", "doc_type": "manual", "allowed_roles": ["technician"]},
        )
        assert upload.status_code == 201, upload.text
        assert upload.json()["chunks"] > 0

        found = await client.get("/documents/search", params={"q": "brine valve fault"})
        assert found.status_code == 200
        assert found.json()["hits"]

    async def test_the_document_list_is_filtered_by_audience(
        self, client: AsyncClient, second_client: object
    ) -> None:
        await login(client, "admin")
        await client.post(
            "/documents",
            files={"file": ("pricing.md", b"# Pricing\n\nDealer cost 2400.\n", "text/markdown")},
            data={"title": "Dealer Pricing", "doc_type": "pricing", "allowed_roles": ["sales"]},
        )

        assert any(
            doc["title"] == "Dealer Pricing" for doc in (await client.get("/documents")).json()
        )

        await login(client, "technician")
        titles = [doc["title"] for doc in (await client.get("/documents")).json()]
        assert "Dealer Pricing" not in titles

    async def test_a_hidden_document_is_a_404_not_a_403(self, client: AsyncClient) -> None:
        """A distinguishable 403 confirms a document with that id exists, which
        is itself information about the corpus."""
        await login(client, "admin")
        created = await client.post(
            "/documents",
            files={"file": ("pricing.md", b"# Pricing\n\nDealer cost 2400.\n", "text/markdown")},
            data={"title": "Dealer Pricing", "doc_type": "pricing", "allowed_roles": ["sales"]},
        )
        document_id = created.json()["document"]["id"]

        await login(client, "technician")

        assert (await client.get(f"/documents/{document_id}")).status_code == 404

    async def test_an_unsupported_file_is_refused(self, client: AsyncClient) -> None:
        await login(client, "admin")

        response = await client.post(
            "/documents",
            files={"file": ("photo.heic", b"\x00\x01", "image/heic")},
            data={"title": "Photo", "doc_type": "manual", "allowed_roles": ["admin"]},
        )

        assert response.status_code == 400
