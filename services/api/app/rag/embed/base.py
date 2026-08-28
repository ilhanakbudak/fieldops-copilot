"""The embedding boundary.

Documents and queries are embedded through the same protocol, but not
necessarily the same call: several modern models expect an asymmetric pair of
prefixes ("represent this passage" vs "represent this query"), and getting that
backwards costs recall silently. Hence two methods rather than one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
