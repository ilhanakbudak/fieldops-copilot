"""semantic cache

An answer kept against the embedding of the question that produced it, so that
"what's the warranty on the radon system" and "how long is the radon system
under warranty" are paid for once.

`audience_key` is in the index and in the lookup because it is part of the
*key*: an answer assembled from one role's documents must never be served to
another. See app/llm/cache.py, which explains why that costs hit rate and is
not negotiable.

No HNSW index here, unlike `chunks`. The table holds one row per distinct
question a business has asked, which is thousands rather than millions, and the
lookup is already narrowed to a single audience before it scores anything. An
approximate index over that is machinery without a problem. The composite index
on `(audience_key, created_at)` is what the lookup actually needs, because every
read is "this audience, not expired".

Revision ID: 0005_semantic_cache
Revises: 0004_revoke_platform_grants
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.models import EMBEDDING_DIM
from app.db.types import Embedding, UtcDateTime

revision: str = "0005_semantic_cache"
down_revision: str | None = "0004_revoke_platform_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cached_answers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("audience_key", sa.String(200), nullable=False),
        sa.Column("term_key", sa.String(300), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("embedding", Embedding(EMBEDDING_DIM), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", sa.Text(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_cached_answers"),
    )
    # `term_key` leads the index because it is the most selective column and
    # the lookup always supplies it: two questions about different fault codes
    # are different rows before their vectors are ever compared.
    op.create_index(
        "ix_cached_answers_lookup",
        "cached_answers",
        ["audience_key", "term_key", "created_at"],
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    # Same reasoning as migration 0002: policies keyed on the role the API sets
    # per transaction, forced so the owner does not bypass its own rules.
    #
    # The read policy is the audience test spelled in SQL. It is redundant with
    # the lookup, which filters on `audience_key` too — and that is the point.
    # A cache is a query, and docs/SECURITY.md's argument is that the boundary
    # belongs in the query *and* underneath it.
    op.execute("ALTER TABLE cached_answers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cached_answers FORCE ROW LEVEL SECURITY")
    # Compared against `app.audience`, a second per-transaction setting the API
    # applies beside `app.role`. Not derived from `app.role` in SQL: that would
    # mean reimplementing `document_roles_for` in a policy, and the day a
    # supervisor role reads two audiences the two copies would disagree — with
    # the database's copy winning silently.
    op.execute(
        """
        CREATE POLICY cached_answers_read ON cached_answers FOR SELECT
          USING (
            current_setting('app.role', true) IN ('admin', 'service')
            OR audience_key = current_setting('app.audience', true)
          )
        """
    )
    op.execute(
        """
        CREATE POLICY cached_answers_write ON cached_answers FOR ALL
          USING (current_setting('app.role', true) IS NOT NULL
                 AND current_setting('app.role', true) <> '')
          WITH CHECK (current_setting('app.role', true) IS NOT NULL
                      AND current_setting('app.role', true) <> '')
        """
    )

    # Migration 0004's reasoning applies to every table this application owns,
    # and a table created after it would otherwise be handed straight back to
    # the platform's default privileges.
    for role in ("anon", "authenticated", "service_role"):
        op.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL PRIVILEGES ON cached_answers FROM {role};
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS cached_answers_write ON cached_answers")
        op.execute("DROP POLICY IF EXISTS cached_answers_read ON cached_answers")
    op.drop_index("ix_cached_answers_lookup", table_name="cached_answers")
    op.drop_table("cached_answers")
