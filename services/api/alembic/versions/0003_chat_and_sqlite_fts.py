"""conversations, and full-text search on both dialects

Two things land here.

**Conversations and their messages**, portable across both dialects.

**Keyword search for SQLite.** Postgres got its generated `tsvector` column and
GIN index in migration 0002; this is the local equivalent, and it is the second
leg of hybrid retrieval — the one that makes `E-04` work.

An FTS5 virtual table with triggers rather than a `LIKE` scan, and with
`tokenchars '-_.'` so that `E-04`, `NG-4200` and `1/2-inch` survive tokenisation
as single terms. The default tokeniser would split `E-04` into `e` and `04`,
which then matches `E-14` on the shared `e` and ranks by whichever happens to be
shorter — the exact failure hybrid search exists to prevent.

Revision ID: 0003_chat_and_sqlite_fts
Revises: 0002_postgres_search_and_rls
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import UtcDateTime

revision: str = "0003_chat_and_sqlite_fts"
down_revision: str | None = "0002_postgres_search_and_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_user_id_updated_at", "conversations", ["user_id", "updated_at"]
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_chat_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(
        "ix_chat_messages_conversation_id_created_at",
        "chat_messages",
        ["conversation_id", "created_at"],
    )

    if op.get_bind().dialect.name == "sqlite":
        _create_sqlite_fts()
    else:
        # Postgres row-level security is enabled per table, so a new table
        # holding employee questions has to be covered explicitly or it is the
        # one place the policies do not reach.
        _secure_postgres_chat()


def _create_sqlite_fts() -> None:
    # `content=''` — an external-content table would need the row ids kept in
    # step with `chunks`, whose primary key is a UUID string. Storing the text
    # twice costs disk this deployment has and removes a whole class of
    # out-of-sync bug.
    op.execute(
        """
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            tokenize = "unicode61 tokenchars '-_.'"
        )
        """
    )
    # Triggers rather than application-side writes: ingestion is not the only
    # thing that touches `chunks` — re-tagging updates them, deleting a document
    # cascades — and an index maintained in only some of those paths is worse
    # than no index at all.
    op.execute(
        """
        CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts (chunk_id, content) VALUES (new.id, new.content);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
            DELETE FROM chunks_fts WHERE chunk_id = old.id;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_fts_update AFTER UPDATE OF content ON chunks BEGIN
            DELETE FROM chunks_fts WHERE chunk_id = old.id;
            INSERT INTO chunks_fts (chunk_id, content) VALUES (new.id, new.content);
        END
        """
    )
    # Backfill anything ingested before this migration.
    op.execute("INSERT INTO chunks_fts (chunk_id, content) SELECT id, content FROM chunks")


def _secure_postgres_chat() -> None:
    for table in ("conversations", "chat_messages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # A conversation belongs to one employee. Nobody else reads it — not even
    # another employee of the same role — because what somebody asked the
    # assistant is more revealing than what they were allowed to read.
    op.execute(
        """
        CREATE POLICY conversations_own ON conversations FOR ALL
          USING (
            user_id = current_setting('app.user_id', true)
            OR current_setting('app.role', true) = 'service'
          )
          WITH CHECK (
            user_id = current_setting('app.user_id', true)
            OR current_setting('app.role', true) = 'service'
          )
        """
    )
    op.execute(
        """
        CREATE POLICY chat_messages_own ON chat_messages FOR ALL
          USING (
            EXISTS (
              SELECT 1 FROM conversations c
               WHERE c.id = chat_messages.conversation_id
                 AND (c.user_id = current_setting('app.user_id', true)
                      OR current_setting('app.role', true) = 'service')
            )
          )
          WITH CHECK (
            EXISTS (
              SELECT 1 FROM conversations c
               WHERE c.id = chat_messages.conversation_id
                 AND (c.user_id = current_setting('app.user_id', true)
                      OR current_setting('app.role', true) = 'service')
            )
          )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for trigger in ("chunks_fts_insert", "chunks_fts_delete", "chunks_fts_update"):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        op.execute("DROP TABLE IF EXISTS chunks_fts")
    else:
        op.execute("DROP POLICY IF EXISTS chat_messages_own ON chat_messages")
        op.execute("DROP POLICY IF EXISTS conversations_own ON conversations")

    op.drop_table("chat_messages")
    op.drop_table("conversations")
