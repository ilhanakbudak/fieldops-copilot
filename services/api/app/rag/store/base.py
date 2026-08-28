"""The vector-store boundary.

The signature below is the whole security argument made mechanical:

    search(query_vector, *, roles, limit, ...)

`roles` has no default. There is no overload without it. An implementation
cannot satisfy this protocol while ignoring it, and a caller cannot forget to
pass it — which is the difference between a role filter and a role convention.

Why this protocol exists at all, given the answer is Postgres: a client asking
"which vector database do you recommend" deserves to see that the recommendation
is a judgement rather than the only thing that was ever wired up. A Weaviate or
Qdrant adapter is this interface and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.auth.rbac import Role


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    page: int | None
    section: str | None
    parent_index: int | None
    score: float


@runtime_checkable
class VectorStore(Protocol):
    name: str

    async def search(
        self,
        query_vector: list[float],
        *,
        roles: frozenset[Role],
        limit: int = 20,
        document_ids: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[SearchHit]:
        """Nearest chunks the caller is allowed to see.

        `roles` restricts the candidate set *before* ranking, not afterwards.
        Post-filtering would let a restricted chunk into the result list, and
        anything that builds a prompt from that list before the filter runs has
        already leaked it into the model's context.
        """
        ...
