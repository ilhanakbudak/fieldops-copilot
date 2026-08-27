"""FastAPI application.

Milestone 1 (retrieval) onward is scaffolded but not implemented; what exists
here is the shape the rest hangs off, plus a health endpoint CI can assert on.
"""

from fastapi import FastAPI
from pydantic import BaseModel

from app.config import Settings, get_settings

app = FastAPI(
    title="FieldOps Copilot API",
    description="Retrieval, connectors and real-time call assistance.",
    version="0.1.0",
)


class HealthResponse(BaseModel):
    """Mirrors HealthResponse in packages/shared."""

    status: str
    demo_mode: bool
    vector_store: str
    embeddings: str
    documents: int


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    settings: Settings = get_settings()
    return HealthResponse(
        status="ok",
        demo_mode=settings.demo_mode,
        vector_store=settings.vector_store,
        embeddings=settings.embedding_provider,
        # Replaced by a real count once ingestion lands.
        documents=0,
    )
