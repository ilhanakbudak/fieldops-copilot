"""Test fixtures.

Every test gets its own SQLite file, migrated from scratch through Alembic. Two
consequences worth having: the migrations are exercised by the whole suite
rather than by one test that remembers to, and no test can be affected by
another's data.

The Postgres-only behaviour — row-level security, full-text search, the HNSW
index — is covered by `tests/test_postgres.py`, which runs against a real
Postgres in CI and skips locally.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role
from app.auth.service import reset_throttle


@pytest.fixture(autouse=True)
def _isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    # The suite exercises the login flow, not Argon2's cost function. At
    # production parameters, seeding four accounts per test would dominate the
    # run time and nothing would be learned from it.
    monkeypatch.setenv("ARGON2_TIME_COST", "1")
    monkeypatch.setenv("ARGON2_MEMORY_KIB", "8")
    # A deterministic stand-in, not a semantic model. The suite asserts that the
    # pipeline wires up and that the role filter holds; asserting that
    # bge-small ranks well would be testing somebody else's model, at the cost
    # of a 130 MB download on every clean CI run.
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hashing")
    monkeypatch.chdir(tmp_path)

    # Imported here rather than at module scope: `get_settings` is cached, and
    # the cache has to be cleared after the environment is set, not before.
    from app.config import get_settings
    from app.connectors import reset_telephony
    from app.rag.embed import reset_embedding_provider
    from app.realtime.hub import reset_call_hub

    get_settings.cache_clear()
    reset_throttle()
    reset_embedding_provider()
    # Both are process state: the mock phone system generates its verification
    # token once, and the hub holds open sockets. A test that inherited either
    # from the one before it would pass or fail on the order it ran in.
    reset_telephony()
    reset_call_hub()
    yield
    get_settings.cache_clear()
    reset_throttle()
    reset_embedding_provider()
    reset_telephony()
    reset_call_hub()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    """Nothing carries a database connection out of a test.

    `app.db.engine` caches its engine and session factory at module scope, and
    the fixture above points each test at a fresh SQLite file. A test that
    touched the database without taking the `client` or `db` fixture — the
    agent-loop tests do, because auditing a tool call is a write — would
    otherwise leave that factory pointing at its own `tmp_path`, and the next
    test to migrate would migrate the previous test's file. The failure lands
    as "no such table: users" in an unrelated file, which is a bad afternoon.
    """
    yield
    from app.db.engine import dispose_engine

    await dispose_engine()


def _new_client() -> AsyncClient:
    from app.main import app

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"user-agent": "pytest"},
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """The application, over ASGI, with the demo accounts seeded.

    Goes through the real lifespan, so the boot path a reviewer runs is the boot
    path the tests run.
    """
    from app.db.engine import dispose_engine
    from app.main import app

    async with _new_client() as http_client, app.router.lifespan_context(app):
        yield http_client
    await dispose_engine()


@pytest.fixture
def second_client() -> Callable[[], AbstractAsyncContextManager[AsyncClient]]:
    """A second browser against the same application.

    Needed wherever the behaviour under test is one person's session being
    changed by somebody else — a demotion, an account being disabled.
    """

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncClient]:
        async with _new_client() as other:
            yield other

    return factory


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    from app.db.engine import dispose_engine, get_sessionmaker
    from app.db.migrate import upgrade_to_head

    await upgrade_to_head()
    async with get_sessionmaker()() as session:
        yield session
        await session.commit()
    await dispose_engine()


async def login(client: AsyncClient, role: Role | str) -> None:
    """Sign in as the demo account for a role; the cookie sticks to the client."""
    from app.db.seed import DEMO_PASSWORD

    role_name = role.value if isinstance(role, Role) else role
    email = {"technician": "tech@example.com"}.get(role_name, f"{role_name}@example.com")

    response = await client.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text


def postgres_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or None
