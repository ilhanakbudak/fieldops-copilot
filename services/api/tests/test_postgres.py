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


async def test_the_pgvector_store_ranks_and_filters_in_one_query(pg: AsyncSession) -> None:
    """The pgvector SQL is never executed on the SQLite path, so without this
    test the production store is code nobody has run."""
    from app.config import get_settings
    from app.rag.embed import build_embedding_provider
    from app.rag.ingest import ingest_document
    from app.rag.store import PgVectorStore

    settings = get_settings()
    embedder = build_embedding_provider(settings)

    await apply_principal(pg, _principal(Role.ADMIN))
    await ingest_document(
        pg,
        data=b"# Softener Manual\n\nError code E-04 indicates a stuck brine valve.\n",
        filename="manual.md",
        title="Softener Manual",
        doc_type="manual",
        allowed_roles=[Role.TECHNICIAN],
        settings=settings,
        embedder=embedder,
    )
    await ingest_document(
        pg,
        data=b"# Dealer Pricing\n\nRadon water system dealer cost is 2400.\n",
        filename="pricing.md",
        title="Dealer Pricing",
        doc_type="pricing",
        allowed_roles=[Role.SALES],
        settings=settings,
        embedder=embedder,
    )

    store = PgVectorStore(pg)
    vector = embedder.embed_query("dealer cost of a radon water system")

    await apply_principal(pg, _principal(Role.ADMIN))
    everything = await store.search(vector, roles=frozenset(Role), limit=10)
    assert {hit.document_title for hit in everything} == {"Softener Manual", "Dealer Pricing"}

    technician = await store.search(vector, roles=frozenset({Role.TECHNICIAN}), limit=10)
    assert {hit.document_title for hit in technician} == {"Softener Manual"}

    # Descending similarity, and a real cosine score rather than a distance.
    assert everything == sorted(everything, key=lambda hit: -hit.score)
    assert all(-1.0 <= hit.score <= 1.0 for hit in everything)


async def test_row_level_security_would_hide_the_chunk_even_without_the_filter(
    pg: AsyncSession,
) -> None:
    """Belt and braces, asserted separately: the store passes a role filter, and
    the database would refuse the row regardless."""
    from app.config import get_settings
    from app.rag.embed import build_embedding_provider
    from app.rag.ingest import ingest_document

    settings = get_settings()
    await apply_principal(pg, _principal(Role.ADMIN))
    await ingest_document(
        pg,
        data=b"# Dealer Pricing\n\nRadon water system dealer cost is 2400.\n",
        filename="pricing.md",
        title="Dealer Pricing",
        doc_type="pricing",
        allowed_roles=[Role.SALES],
        settings=settings,
        embedder=build_embedding_provider(settings),
    )

    # No role filter in this query at all — only the policy stands between the
    # caller and the row.
    await apply_principal(pg, _principal(Role.TECHNICIAN))
    visible = (await pg.execute(text("SELECT count(*) FROM chunks"))).scalar_one()

    assert visible == 0


async def test_the_postgres_keyword_leg_ranks_and_filters(pg: AsyncSession) -> None:
    """`websearch_to_tsquery` and the generated `tsvector` column are Postgres
    only, so this SQL never runs on the SQLite path. Without this test the
    production half of hybrid search is code nobody has executed."""
    from app.config import get_settings
    from app.rag.embed import build_embedding_provider
    from app.rag.ingest import ingest_document
    from app.rag.search.keyword import keyword_search

    settings = get_settings()
    embedder = build_embedding_provider(settings)

    await apply_principal(pg, _principal(Role.ADMIN))
    await ingest_document(
        pg,
        data=(
            b"# Softener Manual\n\n## E-04 Brine Valve Fault\n\n"
            b"The brine draw cycle completed without the expected drop in level. "
            b"Check for a salt bridge in the cabinet.\n"
        ),
        filename="manual.md",
        title="Softener Manual",
        doc_type="manual",
        allowed_roles=[Role.TECHNICIAN],
        settings=settings,
        embedder=embedder,
    )
    await ingest_document(
        pg,
        data=b"# Dealer Pricing\n\nRadon water system dealer cost is 2400 per unit.\n",
        filename="pricing.md",
        title="Dealer Pricing",
        doc_type="pricing",
        allowed_roles=[Role.SALES],
        settings=settings,
        embedder=embedder,
    )

    await apply_principal(pg, _principal(Role.TECHNICIAN))
    hits = await keyword_search(
        pg, "salt bridge brine", roles=frozenset({Role.TECHNICIAN}), limit=10
    )
    assert hits
    assert all(hit.document_title == "Softener Manual" for hit in hits)

    # The role filter is in the query, not applied afterwards.
    leaked = await keyword_search(
        pg, "dealer cost radon", roles=frozenset({Role.TECHNICIAN}), limit=10
    )
    assert all(hit.document_title != "Dealer Pricing" for hit in leaked)

    await apply_principal(pg, _principal(Role.SALES))
    sales = await keyword_search(pg, "dealer cost", roles=frozenset({Role.SALES}), limit=10)
    assert any(hit.document_title == "Dealer Pricing" for hit in sales)


async def test_punctuation_does_not_reach_the_tsquery_parser(pg: AsyncSession) -> None:
    """`to_tsquery` raises on the first question mark anybody types.
    `websearch_to_tsquery` does not, which is the whole reason it is used."""
    from app.rag.search.keyword import keyword_search

    await apply_principal(pg, _principal(Role.TECHNICIAN))

    hits = await keyword_search(
        pg,
        'what does "E-04" mean? (urgent) -- customer waiting & holding',
        roles=frozenset({Role.TECHNICIAN}),
        limit=5,
    )

    assert isinstance(hits, list)


async def test_a_conversation_is_invisible_to_another_employee_in_the_database(
    pg: AsyncSession,
) -> None:
    """Row-level security on `conversations`, asserted with no application
    filter in the query at all."""
    from app.core.clock import utcnow
    from app.db.models import ChatMessage, Conversation

    owner = _principal(Role.TECHNICIAN)
    await apply_principal(pg, owner)

    conversation = Conversation(id=new_id(), user_id=owner.user_id, title="E-04 again")
    pg.add(conversation)
    await pg.flush()
    pg.add(
        ChatMessage(
            id=new_id(),
            conversation_id=conversation.id,
            role="user",
            content="What does E-04 mean?",
            created_at=utcnow(),
        )
    )
    await pg.commit()

    # A different employee — an administrator, even — sees nothing.
    await apply_principal(pg, _principal(Role.ADMIN))
    conversations = (await pg.execute(text("SELECT count(*) FROM conversations"))).scalar_one()
    messages = (await pg.execute(text("SELECT count(*) FROM chat_messages"))).scalar_one()

    assert conversations == 0
    assert messages == 0


def _document(role: Role) -> Document:
    return Document(
        id=new_id(),
        title="Ingested With Nobody Signed In",
        doc_type="manual",
        source_filename="boot.pdf",
        content_hash=new_id().replace("-", ""),
        allowed_roles=[role],
        status="ready",
    )


async def test_the_service_identity_may_write_the_corpus(pg: AsyncSession) -> None:
    """Demo-mode boot, `python -m app.cli seed` and re-indexing all write
    documents with no employee attached.

    The write policies in migration 0002 name `service` alongside `admin` for
    exactly this. Until it was asserted here, nothing in the application ever
    set that role — so seeding a Postgres deployment failed on a policy the
    migration's own comment said would allow it.
    """
    await apply_principal(pg, None, service=True)
    pg.add(_document(Role.TECHNICIAN))
    await pg.commit()

    await apply_principal(pg, _principal(Role.ADMIN))
    count = (await pg.execute(text("SELECT count(*) FROM documents"))).scalar_one()

    assert count == 1


async def test_a_transaction_that_never_said_who_it_was_may_not_write(pg: AsyncSession) -> None:
    """The other half of the same policy, and the reason the escape hatch is a
    keyword argument rather than the default: an anonymous transaction is
    refused rather than quietly privileged."""
    await apply_principal(pg, None)
    pg.add(_document(Role.TECHNICIAN))

    with pytest.raises(ProgrammingError, match="row-level security"):
        await pg.commit()

    await pg.rollback()


async def test_an_identity_is_either_an_employee_or_the_service(pg: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not both"):
        await apply_principal(pg, _principal(Role.ADMIN), service=True)


async def test_the_row_level_security_backstop_is_checked_at_boot(pg: AsyncSession) -> None:
    """CI connects as `fieldops_app`, which has no bypass, so this passes here.

    It is worth having as a test because the interesting case cannot be tested:
    a role *with* `BYPASSRLS` would make every other assertion in this file pass
    while proving nothing, which is exactly the failure the check exists to
    catch. See app/db/health.py.
    """
    from app.config import get_settings
    from app.db.engine import get_engine
    from app.db.health import check_rls_backstop

    assert await check_rls_backstop(get_engine(), get_settings())


async def test_the_platform_roles_hold_no_privileges_on_this_schema(pg: AsyncSession) -> None:
    """Migration 0004, asserted where it can be.

    CI's container has no `anon` role, so the revoke is a no-op here and this
    test is checking the shape rather than the fix. It earns its place anyway:
    if a future migration creates a table and a platform's default privileges
    grant it away, this is the assertion that notices — and the finding it came
    from was that `anon`, the role behind a *publishable* API key, had INSERT on
    `audit_events`, whose append policy allows anyone.
    """
    rows = (
        await pg.execute(
            text(
                "SELECT table_name, grantee, privilege_type "
                "FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' "
                "AND grantee IN ('anon', 'authenticated', 'service_role')"
            )
        )
    ).all()

    assert rows == []


async def test_the_append_only_telemetry_policies_are_the_reason_that_matters(
    pg: AsyncSession,
) -> None:
    """`audit_events` accepts an insert from anyone by design — a failed login
    has no authenticated role, and that attempt is the event worth keeping.

    Which is exactly why no unprivileged role may hold the INSERT *privilege*:
    the policy is permissive on purpose, so the grant is what has to be closed.
    """
    policy = (
        await pg.execute(
            text(
                "SELECT pg_get_expr(polwithcheck, polrelid) FROM pg_policy "
                "WHERE polname = 'audit_events_append'"
            )
        )
    ).scalar_one()

    assert policy == "true"
