"""Local ONNX embeddings.

`BAAI/bge-small-en-v1.5` through FastEmbed: 384 dimensions, quantised ONNX, no
GPU, no API key. Ingesting a 400-page manual costs nothing and reveals nothing
to a third party — which for a corpus of internal procedures and pricing is the
more important of the two.

The model downloads on first use (~130 MB) and is cached afterwards. That delay
is documented rather than hidden, because a silent two-minute pause on the first
question looks exactly like a hang.
"""

from __future__ import annotations

import logging
import threading

from app.config import Settings

logger = logging.getLogger("fieldops.rag.embed")

MODEL = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384

# bge asks for this prefix on queries and nothing on passages. Symmetric
# embedding of both sides measurably loses recall, and the failure is invisible
# — the results are merely a bit worse, forever.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class LocalEmbeddingProvider:
    name = "fastembed"
    model = MODEL
    dimensions = DIMENSIONS

    def __init__(self, settings: Settings) -> None:
        if settings.embedding_dim != DIMENSIONS:
            raise ValueError(
                f"{MODEL} produces {DIMENSIONS} dimensions, "
                f"but EMBEDDING_DIM is {settings.embedding_dim}"
            )
        self._model: object | None = None
        # Loaded once, from whichever request gets there first. The ingestion
        # job and a query can arrive together on a cold process.
        self._lock = threading.Lock()

    def _embedder(self) -> object:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    logger.info("loading %s (first run downloads ~130 MB)", MODEL)
                    self._model = TextEmbedding(model_name=MODEL)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embedder = self._embedder()
        return [vector.tolist() for vector in embedder.embed(texts)]  # type: ignore[attr-defined]

    def embed_query(self, text: str) -> list[float]:
        embedder = self._embedder()
        vectors = list(embedder.embed([QUERY_PREFIX + text]))  # type: ignore[attr-defined]
        return list(vectors[0].tolist())
