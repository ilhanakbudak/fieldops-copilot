"""initial schema

Employees, sessions, documents, chunks, and the two append-only telemetry
tables. Everything here is portable: it applies identically to Supabase Postgres
and to the local SQLite file, which is what keeps the credential-free path
honest rather than a second, quietly different schema.

The Postgres-only machinery — full-text search, the HNSW vector index, row-level
security — is migration 0002.

Revision ID: 0001_initial_schema
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.models import EMBEDDING_DIM
from app.db.types import Embedding, RoleList, UtcDateTime

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Must exist before `chunks.embedding` is declared as vector(n).
        #
        # Checked before creating rather than relying on IF NOT EXISTS, because
        # installing an extension needs elevated privileges and the application
        # role deliberately does not have them. On Supabase the extension is
        # enabled once from the dashboard; this migration then finds it and
        # moves on, instead of failing on a permission it should not hold.
        installed = bind.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        if not installed:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("disabled_at", UtcDateTime(), nullable=True),
        sa.Column("last_login_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("absolute_expires_at", UtcDateTime(), nullable=False),
        sa.Column("last_seen_at", UtcDateTime(), nullable=False),
        sa.Column("revoked_at", UtcDateTime(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(400), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    # Unique, and the lookup every authenticated request performs.
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_id_expires_at", "sessions", ["user_id", "expires_at"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("doc_type", sa.String(60), nullable=False),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("allowed_roles", RoleList(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ingested_at", UtcDateTime(), nullable=True),
        sa.Column("uploaded_by", sa.String(36), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name=op.f("fk_documents_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        # Re-uploading the same PDF is then a detectable conflict rather than a
        # silent doubling of the corpus and of the retrieval noise.
        sa.UniqueConstraint("content_hash", name="uq_documents_content_hash"),
    )
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parent_index", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(500), nullable=True),
        sa.Column("embedding", Embedding(EMBEDDING_DIM), nullable=True),
        # Denormalised from the parent document so the role filter is a
        # predicate on the row the vector index already scans.
        sa.Column("allowed_roles", RoleList(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
    )
    op.create_index("ix_chunks_document_id_parent_index", "chunks", ["document_id", "parent_index"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("occurred_at", UtcDateTime(), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("resource_type", sa.String(60), nullable=True),
        sa.Column("resource_id", sa.String(200), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(400), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index(
        "ix_audit_events_actor_user_id_occurred_at",
        "audit_events",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index("ix_audit_events_action_occurred_at", "audit_events", ["action", "occurred_at"])

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("occurred_at", UtcDateTime(), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("feature", sa.String(60), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_events_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index("ix_usage_events_occurred_at", "usage_events", ["occurred_at"])
    op.create_index(
        "ix_usage_events_user_id_occurred_at", "usage_events", ["user_id", "occurred_at"]
    )
    op.create_index(
        "ix_usage_events_feature_occurred_at", "usage_events", ["feature", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("audit_events")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("sessions")
    op.drop_table("users")
