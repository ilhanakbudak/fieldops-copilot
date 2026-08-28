"""FastAPI application.

Composition happens here and nowhere else: routers, middleware, error handling,
and the demo-mode boot path that makes a fresh clone runnable without a single
credential.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.middleware import RequestContextMiddleware
from app.api.routes import admin, auth
from app.api.schemas import HealthResponse
from app.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.db.engine import dispose_engine, get_sessionmaker, session_scope
from app.db.migrate import upgrade_to_head
from app.db.models import Document
from app.db.seed import seed_demo_users

logger = logging.getLogger("fieldops")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    if settings.demo_mode:
        # A reviewer should be able to clone this and log in, not read a page of
        # setup instructions first. Confined to demo mode: a production
        # deployment migrates as a deliberate step, because a schema change
        # applied automatically by whichever container started first is how
        # migrations go wrong.
        await upgrade_to_head()
        async with session_scope() as db:
            created = await seed_demo_users(db)
        if created:
            logger.info("seeded %d demo accounts", created)

    yield
    await dispose_engine()


app = FastAPI(
    title="FieldOps Copilot API",
    description="Retrieval, connectors and real-time call assistance.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
install_error_handlers(app)

app.include_router(auth.router)
app.include_router(admin.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Unauthenticated on purpose: a load balancer has no session.

    It therefore reports only which way the service is configured, never
    anything about the data.
    """
    settings: Settings = get_settings()

    documents = 0
    try:
        async with get_sessionmaker()() as db:
            documents = int(
                (await db.execute(select(func.count()).select_from(Document))).scalar_one()
            )
    except Exception:
        logger.warning("health check could not read the document count", exc_info=True)

    return HealthResponse(
        status="ok",
        demo_mode=settings.demo_mode,
        vector_store=settings.vector_store,
        embeddings=settings.embedding_provider,
        documents=documents,
    )
