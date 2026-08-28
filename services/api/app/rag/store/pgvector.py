"""Vector search on Postgres.

Cosine distance through pgvector's `<=>` operator, against the HNSW index built
in migration 0002.

The role predicate sits in the same `WHERE` clause as the distance ordering, on
the `allowed_roles` array denormalised onto `chunks`. That is deliberate and it
is the reason the column is denormalised: the planner filters and ranks in one
pass, so there is no arrangement of this query in which the filter is skipped
and the ranking still happens.

Row-level security backs it up underneath — see docs/SECURITY.md. Both, because
the query-level filter is what makes it fast and the policy is what makes it
true.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role
from app.rag.store.base import SearchHit


class PgVectorStore:
    name = "pgvector"

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
        filters = ["c.embedding IS NOT NULL", "c.allowed_roles && :roles"]
        params: dict[str, object] = {
            "roles": [role.value for role in roles],
            "limit": limit,
            # pgvector accepts the literal form; asyncpg has no native binding
            # for the vector type, and this avoids registering a codec purely
            # to pass one parameter.
            "query": "[" + ",".join(f"{value:.7f}" for value in query_vector) + "]",
        }
        if document_ids:
            filters.append("c.document_id = ANY(:document_ids)")
            params["document_ids"] = document_ids
        if doc_types:
            filters.append("d.doc_type = ANY(:doc_types)")
            params["doc_types"] = doc_types

        sql = text(
            f"""
            SELECT c.id, c.document_id, d.title, c.content, c.page, c.section,
                   c.parent_index,
                   1 - (c.embedding <=> CAST(:query AS vector)) AS score
              FROM chunks c
              JOIN documents d ON d.id = c.document_id
             WHERE {" AND ".join(filters)}
             ORDER BY c.embedding <=> CAST(:query AS vector)
             LIMIT :limit
            """
        )

        rows = (await self._session.execute(sql, params)).all()
        return [
            SearchHit(
                chunk_id=row[0],
                document_id=row[1],
                document_title=row[2],
                content=row[3],
                page=row[4],
                section=row[5],
                parent_index=row[6],
                score=float(row[7]),
            )
            for row in rows
        ]
