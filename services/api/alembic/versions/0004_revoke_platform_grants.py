"""revoke the platform's blanket grants on this schema

Postgres-only, and written after running the migrations against a real Supabase
project rather than against a container.

Supabase ships default privileges that grant `anon`, `authenticated` and
`service_role` full DML on every table created in `public` — SELECT, INSERT,
UPDATE and DELETE, automatically, for tables it has never seen. That is a
sensible default for the product it is built for, where PostgREST *is* the API
and row-level security is the whole access model. It is the wrong default here.

This application does not use PostgREST. It owns its own authentication
(AD-1: employee accounts are rows this application owns) and connects with its
own database role. `anon` — the role behind the *publishable* API key, the one
that ends up in a browser — has no business reaching these tables at all.

What that combination actually produced on a live project:

  `audit_events` and `usage_events` carry `FOR INSERT WITH CHECK (true)`, which
  is deliberate: a failed login has no authenticated role, and that attempt is
  precisely the event worth keeping. Combined with a blanket INSERT grant to
  `anon`, anybody holding the public API key could write rows into the audit
  log. A trail the public can forge is not a trail.

  `users` holds Argon2 password hashes and was granted to `anon` as well. What
  stood between the two was Supabase's `ensure_rls` event trigger, which enables
  row-level security on new tables in `public` — so the table ended up protected
  by a platform default rather than by anything in this repository. Relying on
  that is relying on a coincidence.

So the grants are revoked. An unused privilege is not defence in depth; it is an
opening nobody has looked at. Row-level security stays exactly as migration 0002
left it, and is now the second line rather than the only one.

Idempotent, and a no-op anywhere those roles do not exist — CI's plain
`pgvector/pgvector` container, Neon, RDS, a laptop.

Revision ID: 0004_revoke_platform_grants
Revises: 0003_chat_and_sqlite_fts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_revoke_platform_grants"
down_revision: str | None = "0003_chat_and_sqlite_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table this application owns. Named rather than discovered: a loop over
# `information_schema` would also revoke on tables belonging to something else
# sharing the schema, and a table added later ought to be a deliberate edit here.
TABLES = (
    "users",
    "sessions",
    "documents",
    "chunks",
    "audit_events",
    "usage_events",
    "conversations",
    "chat_messages",
    "alembic_version",
)

# The three roles Supabase's default privileges name. `service_role` is a secret
# key rather than a public one, and it is revoked too: nothing in this repository
# uses it, and a credential with standing write access to the audit log is worth
# not having.
ROLES = ("anon", "authenticated", "service_role")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    tables = ", ".join(TABLES)
    for role in ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL PRIVILEGES ON {tables} FROM {role};
                -- Future tables too. Without this the next migration to add one
                -- hands the grant straight back.
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                  REVOKE ALL ON TABLES FROM {role};
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    """Deliberately not a restore.

    Handing `anon` write access to the audit log back is not something a
    downgrade should do quietly. Re-granting is a decision, and it belongs in
    whatever change decides to use PostgREST.
    """
    return
