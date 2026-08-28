"""The Postgres half of the schema.

Row-level security, the generated `tsvector` column and the HNSW index cannot be
exercised against SQLite, so they are covered here and skipped unless
`TEST_DATABASE_URL` points at a Postgres with `pgvector` available. CI runs them
against a `pgvector/pgvector` service container on every push.

Skipping locally is a deliberate trade, and worth stating plainly: without this
file the row-level security policies would be code nobody had ever run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal, Role
from app.core.ids import new_id
from app.db.engine import apply_principal
from app.db.models import Chunk, Document

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a Postgres with pgvector to run these",
)


def _principal(role: Role) -> Principal:
    return Principal(
        user_id=new_id(),
        email=f"{role.value}@example.com",
        full_name=role.value.title(),
        role=role,
        session_id=new_id(),
    )


@pytest_asyncio.fixture
async def pg(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    assert TEST_DATABASE_URL
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db.engine import dispose_engine, get_sessionmaker
    from app.db.migrate import downgrade_to_base, upgrade_to_head

    # Unwind and reapply, so every run exercises the migrations in both
    # directions rather than inheriting whatever the last run left behind.
    # Not `DROP SCHEMA public` — the application role does not own the schema,
    # which is the point of connecting as it rather than as a superuser.
    await downgrade_to_base()
    await upgrade_to_head()

    async with get_sessionmaker()() as session:
        yield session
    await dispose_engine()
    get_settings.cache_clear()


async def _seed_corpus(session: AsyncSession) -> None:
    """One document per audience, each with one chunk."""
    await apply_principal(session, _principal(Role.ADMIN))
    for role, title, body in (
        (
            Role.TECHNICIAN,
            "Softener Service Manual",
            "Error code E-04 indicates a stuck brine valve.",
        ),
        (Role.SALES, "Dealer Price List", "Radon water system, dealer cost 2,400."),
    ):
        document = Document(
            id=new_id(),
            title=title,
            doc_type="manual" if role is Role.TECHNICIAN else "pricing",
            source_filename=f"{title}.pdf",
            content_hash=new_id().replace("-", ""),
            allowed_roles=[role],
            status="ready",
        )
        session.add(document)
        session.add(
            Chunk(
                id=new_id(),
                document_id=document.id,
                ordinal=0,
                content=body,
                page=1,
                allowed_roles=[role],
            )
        )
    await session.commit()


async def test_row_level_security_hides_documents_from_the_wrong_role(
    pg: AsyncSession,
) -> None:
    """The retrieval layer filters by role as well. This asserts the database
    would refuse even if that filter were dropped."""
    await _seed_corpus(pg)

    await apply_principal(pg, _principal(Role.TECHNICIAN))
    titles = list((await pg.execute(text("SELECT title FROM documents"))).scalars())

    assert titles == ["Softener Service Manual"]


async def test_row_level_security_hides_chunks_from_the_wrong_role(pg: AsyncSession) -> None:
    """Chunks matter more than documents: a leaked chunk goes into the model's
    context and therefore into its answer, whether or not the UI shows it."""
    await _seed_corpus(pg)

    await apply_principal(pg, _principal(Role.TECHNICIAN))
    contents = list((await pg.execute(text("SELECT content FROM chunks"))).scalars())

    assert len(contents) == 1
    assert "dealer cost" not in contents[0]


async def test_an_admin_sees_every_audience(pg: AsyncSession) -> None:
    await _seed_corpus(pg)

    await apply_principal(pg, _principal(Role.ADMIN))
    count = (await pg.execute(text("SELECT count(*) FROM documents"))).scalar_one()

    assert count == 2


async def test_an_unauthenticated_connection_sees_nothing(pg: AsyncSession) -> None:
    """`current_setting('app.role', true)` is NULL, and NULL matches no policy."""
    await _seed_corpus(pg)

    await apply_principal(pg, None)
    count = (await pg.execute(text("SELECT count(*) FROM documents"))).scalar_one()

    assert count == 0


async def test_a_non_admin_cannot_write_documents(pg: AsyncSession) -> None:
    await _seed_corpus(pg)
    await apply_principal(pg, _principal(Role.TECHNICIAN))

    with pytest.raises(ProgrammingError):
        await pg.execute(
            text(
                "INSERT INTO documents "
                "(id, title, doc_type, source_filename, content_hash, allowed_roles,"
                " status, created_at, updated_at) "
                "VALUES ('x', 't', 'manual', 'f.pdf', 'h', ARRAY['technician'],"
                " 'ready', now(), now())"
            )
        )
    await pg.rollback()


async def test_full_text_search_finds_an_exact_error_code(pg: AsyncSession) -> None:
    """The reason the retriever is hybrid rather than pure vector search: `E-04`
    and `E-14` are near-identical embeddings, and this is not."""
    await _seed_corpus(pg)
    await apply_principal(pg, _principal(Role.ADMIN))

    hits = (
        await pg.execute(
            text(
                "SELECT count(*) FROM chunks "
                "WHERE content_tsv @@ websearch_to_tsquery('english', :q)"
            ),
            {"q": "E-04"},
        )
    ).scalar_one()

    assert hits == 1


async def test_the_expected_indexes_exist(pg: AsyncSession) -> None:
    """Cheap, and it catches a migration that ran but silently skipped its
    Postgres-only half."""
    names = set(
        (
            await pg.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'"))
        ).scalars()
    )

    assert "ix_chunks_embedding_hnsw" in names
    assert "ix_chunks_content_tsv" in names
    assert "ix_chunks_allowed_roles" in names
