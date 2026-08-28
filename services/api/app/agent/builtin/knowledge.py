"""Searching the knowledge base, as a tool.

The whole of milestone 3 — analysis, hybrid search, fusion, reranking, parent
expansion — sits behind one function the model may choose to call. That is the
change agentic routing makes: retrieval stops being what the endpoint does and
becomes one of the things it can decide to do.

The passages come back in the same rendered form the prompt used before, markers
and all, so citation resolution downstream is unchanged. What changed is only
*when* it runs.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools import ToolContext, ToolResult
from app.auth.rbac import Permission
from app.config import get_settings
from app.llm import get_llm_provider
from app.rag.cite import render_sources
from app.rag.search.pipeline import retrieve


class SearchKnowledgeBase:
    name = "search_knowledge_base"
    description = (
        "Search the company's internal documents — equipment manuals, standard "
        "operating procedures, warranty policies, pricing and troubleshooting "
        "guides. Use this for anything about company procedure, equipment, error "
        "codes, warranty terms or water treatment. Do not use it for the current "
        "date or time, or for looking up a customer."
    )
    # A class attribute rather than a ClassVar annotation: the protocol
    # declares `parameters` as an instance member so that an adapter can set
    # it per instance, which is how MCP tools carry their own schema.
    parameters = {  # noqa: RUF012 - read-only schema, never mutated
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question, as a self-contained search query.",
            }
        },
        "required": ["query"],
    }
    permission: Permission | None = Permission.DOCUMENTS_READ

    async def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(content="No query was given.", summary="Empty search", ok=False)

        settings = get_settings()
        result = await retrieve(
            context.session,
            query,
            # Not optional, and not derived from anything the model said. The
            # model chooses whether to search; it does not choose whose
            # documents to search.
            context.principal,
            settings=settings,
            llm=get_llm_provider(),
        )

        if not result.passages:
            return ToolResult(
                content=(
                    "No passage in the documents this employee may read answers that. "
                    "Say so plainly; do not answer from general knowledge."
                ),
                summary=f"Searched the knowledge base for “{query}” — nothing matched",
                data={"sources": [], "query": query},
            )

        return ToolResult(
            content=render_sources(result.passages),
            summary=(
                f"Searched {result.candidates} passages, kept {len(result.passages)} "
                f"({result.duration_ms}ms)"
            ),
            data={
                "query": query,
                "rewritten": result.analysis.rewritten,
                "retrievalMs": result.duration_ms,
                "candidates": result.candidates,
                "reranker": result.reranker,
                "passages": result.passages,
            },
        )
