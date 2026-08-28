"""postgres search indexes and row-level security

Everything in this migration is Postgres-only and skipped on SQLite, which is
the local fallback rather than the deployment target. Three things land here:

**Full-text search.** Half the real questions are keyword lookups on a part
number or an error code, and `E-04` and `E-14` are near-identical vectors. A
stored generated `tsvector` column with a GIN index gives the hybrid retriever
its second leg. Generated and stored rather than computed per query, because
`to_tsvector` over a corpus of manuals at query time does not scale and cannot
use an index.

**An HNSW vector index** rather than IVFFlat: no training step, better recall at
the same probe cost, and the corpus keeps changing as documents are uploaded —
IVFFlat's centroids would need periodic rebuilding to stay accurate.

**Row-level security**, which is the point of choosing Postgres for this at all.
The retrieval layer already filters by role; these policies mean the database
refuses to return a restricted chunk even if that filter is ever dropped by
someone optimising a query. Defence in depth, in the one place that cannot be
forgotten.

Deliberately *not* covered by RLS: `users` and `sessions`. Authentication has to
read those rows in order to work out who is asking, so a policy keyed on the
caller's identity would be circular.

Revision ID: 0002_postgres_search_and_rls
Revises: 0001_initial_schema
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_postgres_search_and_rls"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Set per transaction by the API (`SET LOCAL app.role = …`, see
# app/db/engine.py). `true` as the second argument makes `current_setting`
# return NULL instead of raising when the setting is absent — which is the case
# for an unauthenticated request, and must not be an error.
_ROLE = "current_setting('app.role', true)"

# Background work — ingestion, re-indexing, seeding — runs with no employee
# attached. It identifies itself as 'service' rather than being handed a blanket
# exemption, so the escape hatch is explicit and greppable.
_IS_PRIVILEGED = f"({_ROLE} = 'admin' OR {_ROLE} = 'service')"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # --- Full-text search --------------------------------------------------
    op.execute(
        """
        ALTER TABLE chunks
          ADD COLUMN content_tsv tsvector
          GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """
    )
    op.execute("CREATE INDEX ix_chunks_content_tsv ON chunks USING GIN (content_tsv)")

    # --- Vector search -----------------------------------------------------
    op.execute(
        """
        CREATE INDEX ix_chunks_embedding_hnsw ON chunks
          USING hnsw (embedding vector_cosine_ops)
          WITH (m = 16, ef_construction = 64)
        """
    )
    # The role filter runs alongside the vector scan, so it wants an index of
    # its own on the array-overlap operator.
    op.execute("CREATE INDEX ix_chunks_allowed_roles ON chunks USING GIN (allowed_roles)")
    op.execute("CREATE INDEX ix_documents_allowed_roles ON documents USING GIN (allowed_roles)")

    # --- Row-level security ------------------------------------------------
    # FORCE, not just ENABLE: without it the table owner — which is the role the
    # application connects as — bypasses its own policies, and the whole
    # exercise protects nobody.
    for table in ("documents", "chunks", "audit_events", "usage_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(
        f"""
        CREATE POLICY documents_read ON documents FOR SELECT
          USING ({_IS_PRIVILEGED} OR {_ROLE} = ANY (allowed_roles))
        """
    )
    op.execute(
        f"""
        CREATE POLICY chunks_read ON chunks FOR SELECT
          USING ({_IS_PRIVILEGED} OR {_ROLE} = ANY (allowed_roles))
        """
    )
    for table in ("documents", "chunks"):
        op.execute(
            f"""
            CREATE POLICY {table}_write ON {table} FOR ALL
              USING ({_IS_PRIVILEGED}) WITH CHECK ({_IS_PRIVILEGED})
            """
        )

    # Telemetry is append-only and readable by administrators. The insert policy
    # is unconditional on purpose: a failed login has no authenticated role, and
    # that attempt is precisely the event worth keeping.
    for table in ("audit_events", "usage_events"):
        op.execute(f"CREATE POLICY {table}_append ON {table} FOR INSERT WITH CHECK (true)")
        op.execute(f"CREATE POLICY {table}_read ON {table} FOR SELECT USING ({_IS_PRIVILEGED})")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table, policies in (
        ("documents", ("documents_read", "documents_write")),
        ("chunks", ("chunks_read", "chunks_write")),
        ("audit_events", ("audit_events_append", "audit_events_read")),
        ("usage_events", ("usage_events_append", "usage_events_read")),
    ):
        for policy in policies:
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS ix_documents_allowed_roles")
    op.execute("DROP INDEX IF EXISTS ix_chunks_allowed_roles")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS content_tsv")
