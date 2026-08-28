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
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.rbac import Principal
from app.config import Settings, get_settings

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


async def apply_principal(session: AsyncSession, principal: Principal | None) -> None:
    """Tell Postgres who is asking.

    The row-level security policies in the migration read these settings, which
    makes the database itself the last line of defence: if a query in the
    retrieval layer ever forgets its role filter, Postgres still refuses to
    return a document the caller's role is not tagged for.

    `SET LOCAL` scopes the values to the current transaction, so a pooled
    connection cannot leak one request's identity into the next. On SQLite this
    is a no-op — the local fallback has application-level filtering only, which
    is stated plainly in docs/SECURITY.md rather than glossed over.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return

    if principal is None:
        await session.execute(text("SELECT set_config('app.user_id', '', true)"))
        await session.execute(text("SELECT set_config('app.role', '', true)"))
        return

    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": principal.user_id},
    )
    await session.execute(
        text("SELECT set_config('app.role', :role, true)"),
        {"role": principal.role.value},
    )


@asynccontextmanager
async def session_scope(principal: Principal | None = None) -> AsyncIterator[AsyncSession]:
    """A transaction that commits on success and rolls back on anything else."""
    async with get_sessionmaker()() as session:
        await apply_principal(session, principal)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
