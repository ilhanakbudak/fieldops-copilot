"""Selecting a vector store."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.rag.store.base import SearchHit, VectorStore
from app.rag.store.pgvector import PgVectorStore
from app.rag.store.sqlite import SqliteVectorStore

__all__ = [
    "PgVectorStore",
    "SearchHit",
    "SqliteVectorStore",
    "VectorStore",
    "vector_store_for",
]


def vector_store_for(session: AsyncSession, settings: Settings | None = None) -> VectorStore:
    """Bound to the caller's session, not to the engine.

    That matters on Postgres: row-level security reads settings applied with
    `SET LOCAL` on this transaction, so a store holding its own connection
    would search as nobody and find nothing.
    """
    settings = settings or get_settings()
    return PgVectorStore(session) if settings.is_postgres else SqliteVectorStore(session)
