"""Vector search without a vector database.

Exact brute-force cosine over every chunk the caller is allowed to see. No
index, no approximation, and no third dependency.

This is the credential-free path, and its limits are the point rather than an
embarrassment. At a few thousand chunks it is fast and it is *exactly* right —
it returns the true nearest neighbours, which makes it a useful oracle to check
the approximate index against. At a few hundred thousand it would be hopeless,
which is precisely the work pgvector's HNSW does in production.

The role filter still runs in SQL rather than in Python, for the same reason as
the Postgres store: filtering after the fact means the restricted rows were read
and are one careless refactor away from being used.
"""

from __future__ import annotations

import json
import struct

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role
from app.rag.store.base import SearchHit


class SqliteVectorStore:
    name = "sqlite"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        query_vector: list[float],
        *,
        roles: frozenset[Role],
        limit: int = 20,
        document_ids: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[SearchHit]:
        wanted = {role.value for role in roles}

        filters = ["c.embedding IS NOT NULL"]
        params: dict[str, object] = {}
        if document_ids:
            placeholders = ",".join(f":doc{i}" for i in range(len(document_ids)))
            filters.append(f"c.document_id IN ({placeholders})")
            params |= {f"doc{i}": value for i, value in enumerate(document_ids)}
        if doc_types:
            placeholders = ",".join(f":type{i}" for i in range(len(doc_types)))
            filters.append(f"d.doc_type IN ({placeholders})")
            params |= {f"type{i}": value for i, value in enumerate(doc_types)}

        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT c.id, c.document_id, d.title, c.content, c.page,
                           c.section, c.parent_index, c.embedding, c.allowed_roles
                      FROM chunks c
                      JOIN documents d ON d.id = c.document_id
                     WHERE {" AND ".join(filters)}
                    """
                ),
                params,
            )
        ).all()

        if not rows:
            return []

        # SQLite has no array type, so `allowed_roles` is a JSON list and the
        # overlap test happens here. Still before scoring, so a chunk the caller
        # may not see is never ranked and never returned.
        visible = [row for row in rows if wanted & set(json.loads(row[8]))]
        if not visible:
            return []

        dimensions = len(query_vector)
        matrix = np.array(
            [struct.unpack(f"{dimensions}f", row[7]) for row in visible], dtype=np.float32
        )
        query = np.array(query_vector, dtype=np.float32)

        # Cosine, computed explicitly: the stored vectors are whatever the
        # provider produced, and assuming they arrive unit-length is the kind of
        # assumption that silently reorders results when a provider changes.
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
        norms[norms == 0] = 1.0
        scores = (matrix @ query) / norms

        top = np.argsort(-scores)[:limit]
        return [
            SearchHit(
                chunk_id=visible[index][0],
                document_id=visible[index][1],
                document_title=visible[index][2],
                content=visible[index][3],
                page=visible[index][4],
                section=visible[index][5],
                parent_index=visible[index][6],
                score=float(scores[index]),
            )
            for index in top
        ]
