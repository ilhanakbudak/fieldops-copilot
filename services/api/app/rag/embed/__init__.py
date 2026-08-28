"""Selecting an embedding provider."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.rag.embed.base import EmbeddingProvider
from app.rag.embed.hashing import HashingEmbeddingProvider
from app.rag.embed.local import LocalEmbeddingProvider
from app.rag.embed.openai import OpenAiEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "LocalEmbeddingProvider",
    "OpenAiEmbeddingProvider",
    "build_embedding_provider",
    "get_embedding_provider",
    "reset_embedding_provider",
]

_provider: EmbeddingProvider | None = None


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        return OpenAiEmbeddingProvider(settings)
    if settings.embedding_provider == "hashing":
        return HashingEmbeddingProvider(settings.embedding_dim)
    return LocalEmbeddingProvider(settings)


def get_embedding_provider() -> EmbeddingProvider:
    """One instance per process: the local model holds ~130 MB of weights, and
    loading a second copy per request would be the whole memory budget."""
    global _provider
    if _provider is None:
        _provider = build_embedding_provider(get_settings())
    return _provider


def reset_embedding_provider() -> None:
    global _provider
    _provider = None
