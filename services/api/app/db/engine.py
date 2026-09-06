"""Engine and session factory.

One engine per process, created lazily so importing the application does not
open a connection — which matters for tests, for Alembic, and for any tooling
that imports `app` to read metadata.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from app.auth.rbac import Principal
from app.config import Settings, get_settings
from app.db.roles import audience_key

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs(settings: Settings) -> dict[str, Any]:
    if settings.is_postgres:
        return {
            "pool_size": 10,
            "max_overflow": 20,
            # Supabase's pooler closes idle connections; recycling under its
            # timeout avoids handing the application a dead one.
            "pool_recycle": 900,
            "pool_pre_ping": True,
        }
    # SQLite: one file, no pooling story worth having.
    return {}


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.sqlalchemy_url,
            echo=False,
            future=True,
            **_engine_kwargs(settings),
        )
        if not settings.is_postgres:
            _configure_sqlite(_engine)
    return _engine


def _configure_sqlite(engine: AsyncEngine) -> None:
    """SQLite defaults are wrong for a web service in two ways.

    Foreign keys are off unless asked for, so `ON DELETE CASCADE` silently does
    nothing — which would leave sessions alive after their user was deleted.
    And the rollback journal blocks readers behind a writer, which WAL fixes.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Close the pool on shutdown, and reset the module state so tests can point
    the application at a different database within one process."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


# Where the identity applied by `apply_principal` is remembered, so that
# `_reapply_identity` can put it back. A key on `Session.info` rather than
# module state, because two sessions in one process are two different callers.
_IDENTITY = "fieldops.identity"

# One statement rather than three, because it runs on every transaction rather
# than once per session — three round trips to Supabase's pooler per transaction
# is a cost worth not paying for punctuation.
_SET_IDENTITY = text(
    "SELECT set_config('app.user_id', :user_id, true), "
    "set_config('app.role', :role, true), "
    "set_config('app.audience', :audience, true)"
)


async def apply_principal(
    session: AsyncSession, principal: Principal | None, *, service: bool = False
) -> None:
    """Tell Postgres who is asking.

    The row-level security policies in the migration read these settings, which
    makes the database itself the last line of defence: if a query in the
    retrieval layer ever forgets its role filter, Postgres still refuses to
    return a document the caller's role is not tagged for.

    `SET LOCAL` scopes the values to the current transaction, so a pooled
    connection cannot leak one request's identity into the next. It also means
    the values are gone the moment that transaction ends, which is why the
    identity is remembered on the session and re-applied by `_reapply_identity`
    below. On SQLite this is a no-op — the local fallback has
    application-level filtering only, which is stated plainly in
    docs/SECURITY.md rather than glossed over.

    `service=True` is the one identity that is not an employee: ingestion,
    re-indexing and seeding write the corpus with nobody signed in, and the
    policies in migration 0002 name `service` alongside `admin` for exactly
    that. It is a keyword argument rather than a role on `Principal` because a
    role on `Principal` would also be a document audience, and a caller must
    never be able to become one by having their role changed.
    """
    if service and principal is not None:
        raise ValueError("A transaction is either an employee's or the service's, not both.")

    if session.bind is None or session.bind.dialect.name != "postgresql":
        return

    if service:
        identity = _identity(user_id="", role="service", audience="")
    elif principal is None:
        identity = _identity(user_id="", role="", audience="")
    else:
        # `app.audience` alongside `app.role`, because one policy needs the set
        # of document audiences this caller may read rather than the name of
        # their role. Sent rather than derived in SQL: deriving it would put a
        # copy of `document_roles_for` in a policy, and two copies of an access
        # rule is one more than is safe.
        identity = _identity(
            user_id=principal.user_id,
            role=principal.role.value,
            audience=audience_key(principal.document_roles),
        )

    session.info[_IDENTITY] = identity
    await session.execute(_SET_IDENTITY, identity)


def _identity(*, user_id: str, role: str, audience: str) -> dict[str, str]:
    """The bind parameters of `_SET_IDENTITY`, kept together so that what is
    remembered on the session and what is sent to Postgres cannot drift."""
    return {"user_id": user_id, "role": role, "audience": audience}


@event.listens_for(Session, "after_begin")
def _reapply_identity(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    """Put the caller's identity back on every transaction the session opens.

    `SET LOCAL` dies with its transaction, and a session outlives its
    transactions. Ingestion is the case that proves it: the document row is
    committed as `processing` before indexing starts (see app/rag/ingest.py),
    and every statement after that commit would otherwise run on a transaction
    that never said who it was — so the policies would refuse to write the
    chunks, correctly, and uploading a document to a Postgres deployment would
    fail with a stale-data error nobody could read.

    Done here rather than by re-applying at each call site that commits: a rule
    that must be remembered every time somebody adds a commit is a rule that
    will eventually be forgotten, and forgetting it fails only on Postgres,
    which is the deployment target and not the local fallback.
    """
    if connection.dialect.name != "postgresql":
        return

    identity: dict[str, str] | None = session.info.get(_IDENTITY)
    if identity is None:
        return

    connection.execute(_SET_IDENTITY, identity)


@asynccontextmanager
async def session_scope(
    principal: Principal | None = None, *, service: bool = False
) -> AsyncIterator[AsyncSession]:
    """A transaction that commits on success and rolls back on anything else.

    Used outside HTTP — the CLI, ingestion, the demo-mode boot path. Those
    write the corpus with no employee signed in, so they pass `service=True`;
    without it Postgres refuses the insert, which is the row-level security
    policies working correctly on a caller that never said who it was. The
    escape hatch is a keyword rather than a default so it is greppable.

    The telemetry buffer is opened around it for the same reason the middleware
    opens one per request: an `audit()` call made while this transaction is open
    must not try to write on a second connection, or SQLite deadlocks against
    itself.
    """
    # Imported here rather than at module scope: the audit log needs this
    # module's session factory, so a top-level import would be circular.
    from app.audit.log import telemetry_unit

    async with telemetry_unit(), get_sessionmaker()() as session:
        await apply_principal(session, principal, service=service)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
