"""OpenAI embeddings.

`text-embedding-3-small`, asked for **384** dimensions rather than its native
1536. That is not a downgrade taken to save space: the model is trained with
Matryoshka representation learning, so a truncated prefix of the vector is
itself a valid embedding, and the shortened form keeps almost all of the
retrieval quality.

The reason to do it is that the database column then has one width. A deployment
can move between local and OpenAI embeddings — or fall back during an outage —
without a migration and a full re-index.
"""

from __future__ import annotations

import httpx

from app.config import Settings

ENDPOINT = "https://api.openai.com/v1/embeddings"

# Comfortably inside the request limit, and large enough that a 400-page manual
# is tens of calls rather than thousands.
BATCH = 128


class OpenAiEmbeddingProvider:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dim
        self._key = settings.openai_api_key

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            vectors.extend(self._post(texts[start : start + BATCH]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._post([text])[0]

    def _post(self, inputs: list[str]) -> list[list[float]]:
        response = httpx.post(
            ENDPOINT,
            headers={"authorization": f"Bearer {self._key}"},
            json={"model": self.model, "input": inputs, "dimensions": self.dimensions},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        # Sorted by index: the API does not promise to return them in order, and
        # a silently reordered batch pairs every chunk with somebody else's
        # vector.
        rows = sorted(payload["data"], key=lambda row: row["index"])
        return [row["embedding"] for row in rows]
