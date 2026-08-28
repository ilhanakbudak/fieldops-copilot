"""The keyword leg of hybrid search.

This is the half that makes `E-04` work.

Embeddings place `E-04` and `E-14` almost on top of each other — they are two
characters apart in a space built to collapse surface differences, which is
exactly what it is supposed to do and exactly wrong for a part number. Lexical
search has the opposite bias: it cannot tell that "warm water" and "elevated
temperature" are the same idea, and it can tell `E-04` from `E-14` perfectly.

Neither is sufficient. Running both and fusing the ranks is.

The role filter is in the query here for the same reason it is in the vector
store: a chunk excluded after the fact has already been read.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role
from app.rag.store.base import SearchHit

# FTS5 treats these as operators. A question mark or a quote in an employee's
# question is not an operator, and letting it reach the parser turns a search
# into a syntax error.
_FTS5_SAFE = re.compile(r"[^\w\-/.]+")


async def keyword_search(
    session: AsyncSession,
    query: str,
    *,
    roles: frozenset[Role],
    limit: int = 40,
    doc_types: list[str] | None = None,
) -> list[SearchHit]:
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "postgresql":
        return await _postgres(session, query, roles=roles, limit=limit, doc_types=doc_types)
    return await _sqlite(session, query, roles=roles, limit=limit, doc_types=doc_types)


async def _postgres(
    session: AsyncSession,
    query: str,
    *,
    roles: frozenset[Role],
    limit: int,
    doc_types: list[str] | None,
) -> list[SearchHit]:
    filters = ["c.allowed_roles && :roles", "c.content_tsv @@ q"]
    params: dict[str, object] = {
        "roles": [role.value for role in roles],
        "query": query,
        "limit": limit,
    }
    if doc_types:
        filters.append("d.doc_type = ANY(:doc_types)")
        params["doc_types"] = doc_types

    # `websearch_to_tsquery` rather than `plainto_tsquery`: it understands
    # quoted phrases and `-exclusions` the way a person expects a search box to,
    # and it never raises on punctuation — which `to_tsquery` does, on the first
    # question mark anybody types.
    sql = text(
        f"""
        SELECT c.id, c.document_id, d.title, c.content, c.page, c.section,
               c.parent_index, ts_rank_cd(c.content_tsv, q) AS score
          FROM chunks c
          JOIN documents d ON d.id = c.document_id,
               websearch_to_tsquery('english', :query) q
         WHERE {" AND ".join(filters)}
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    return [_hit(row) for row in (await session.execute(sql, params)).all()]


async def _sqlite(
    session: AsyncSession,
    query: str,
    *,
    roles: frozenset[Role],
    limit: int,
    doc_types: list[str] | None,
) -> list[SearchHit]:
    match = _fts5_query(query)
    if not match:
        return []

    # SQLite has no array type, so `allowed_roles` is a JSON list and the
    # overlap test is a set of LIKEs. Ugly, and still in the query rather than
    # in Python, because the alternative is reading rows the caller may not see.
    role_clauses = " OR ".join(f"c.allowed_roles LIKE :role{i}" for i in range(len(roles)))
    filters = [f"({role_clauses})"]
    params: dict[str, object] = {
        f"role{i}": f'%"{role.value}"%' for i, role in enumerate(sorted(roles))
    }
    params |= {"match": match, "limit": limit}

    if doc_types:
        placeholders = ",".join(f":type{i}" for i in range(len(doc_types)))
        filters.append(f"d.doc_type IN ({placeholders})")
        params |= {f"type{i}": value for i, value in enumerate(doc_types)}

    # bm25() returns a negative number where more negative is better; negating
    # it gives an ordinary "higher is better" score, which is what rank fusion
    # downstream assumes of every leg.
    sql = text(
        f"""
        SELECT c.id, c.document_id, d.title, c.content, c.page, c.section,
               c.parent_index, -bm25(chunks_fts) AS score
          FROM chunks_fts
          JOIN chunks c ON c.id = chunks_fts.chunk_id
          JOIN documents d ON d.id = c.document_id
         WHERE chunks_fts MATCH :match AND {" AND ".join(filters)}
         ORDER BY score DESC
         LIMIT :limit
        """
    )
    return [_hit(row) for row in (await session.execute(sql, params)).all()]


def _fts5_query(query: str) -> str:
    """Turn a question into an FTS5 MATCH expression.

    Terms are OR-ed, not AND-ed. A full sentence rarely has every word in one
    passage, and requiring that returns nothing at all — which looks like a
    broken index rather than a strict one. Ranking sorts out relevance; the
    query's job is to produce candidates.
    """
    terms = [term for term in _FTS5_SAFE.split(query) if len(term) > 1]
    if not terms:
        return ""
    # Quoted, so a term containing `-` is read as a string rather than as the
    # NOT operator.
    return " OR ".join(f'"{term}"' for term in terms)


def _hit(row: Row[Any]) -> SearchHit:
    values = tuple(row)
    return SearchHit(
        chunk_id=values[0],
        document_id=values[1],
        document_title=values[2],
        content=values[3],
        page=values[4],
        section=values[5],
        parent_index=values[6],
        score=float(values[7]),
    )
