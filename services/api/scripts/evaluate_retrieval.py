"""Score the retrieval pipeline against the evaluation set.

    uv run python scripts/evaluate_retrieval.py
    uv run python scripts/evaluate_retrieval.py --compare      # ablation

Why this exists: every change to chunking, embeddings, fusion or reranking feels
like an improvement while you are making it. Without a number, "I improved
retrieval" means "I changed retrieval". The set is twenty hand-written cases
against the synthetic corpus — small, and the point is the direction it moves
rather than the absolute value.

Three metrics:

**recall@k** — did a passage that could answer the question make the top k? The
one that matters: a passage never retrieved cannot be cited, and no amount of
generation quality recovers it.

**MRR** — where in the list. A correct passage at rank 6 is one the model reads
last, after five less relevant ones.

**Role isolation** — the cases that name documents a role must never retrieve.
Note that this is not the same as expecting an empty result: sales cannot read
the service manual but may perfectly well retrieve the warranty policy for the
same question. A retrieval system that scores well on recall and leaks pricing
to technicians has failed at the only part that cannot be fixed later.

`--compare` re-runs the set with reranking disabled and with the exact-term
boost removed, which is how the numbers quoted in docs/RAG.md were arrived at
rather than assumed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL_SET = Path(__file__).resolve().parents[3] / "fixtures" / "eval" / "retrieval.json"


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    question: str
    role: str
    hit_rank: int | None
    expect_empty: bool
    forbidden: list[str]
    retrieved: list[str]
    documents: list[str]

    @property
    def leaked(self) -> list[str]:
        return sorted({title for title in self.documents if title in set(self.forbidden)})

    @property
    def is_isolation_case(self) -> bool:
        return self.expect_empty or bool(self.forbidden)

    @property
    def passed(self) -> bool:
        if self.expect_empty:
            return not self.retrieved
        if self.forbidden:
            return not self.leaked
        return self.hit_rank is not None


async def _run(top_k: int, *, rerank: str, boost: bool) -> list[CaseResult]:
    from app.auth.rbac import Principal, Role
    from app.config import get_settings
    from app.core.ids import new_id
    from app.db.engine import get_sessionmaker
    from app.llm import get_llm_provider
    from app.rag.search import pipeline as pipeline_module
    from app.rag.search.rerank import build_reranker

    settings = get_settings()
    reranker = build_reranker(settings.model_copy(update={"rerank_provider": rerank}))

    # Monkey-patching for an ablation is acceptable in a measurement script and
    # would not be in the application. It is restored in the `finally`.
    original = pipeline_module._exact_matches
    if not boost:
        pipeline_module._exact_matches = lambda content, keywords: 0  # type: ignore[assignment]

    cases = json.loads(EVAL_SET.read_text())["cases"]
    results: list[CaseResult] = []

    try:
        async with get_sessionmaker()() as session:
            for case in cases:
                principal = Principal(
                    user_id=new_id(),
                    email=f"{case['role']}@example.com",
                    full_name=case["role"].title(),
                    role=Role(case["role"]),
                    session_id=new_id(),
                )
                result = await pipeline_module.retrieve(
                    session,
                    case["question"],
                    principal,
                    settings=settings,
                    llm=get_llm_provider(),
                    reranker=reranker,
                    top_k=top_k,
                )

                sections = [passage.section or "" for passage in result.passages]
                expected = set(case.get("expect_sections", []))
                rank = next(
                    (index for index, section in enumerate(sections, 1) if section in expected),
                    None,
                )
                results.append(
                    CaseResult(
                        case_id=case["id"],
                        question=case["question"],
                        role=case["role"],
                        hit_rank=rank,
                        expect_empty=bool(case.get("expect_empty")),
                        forbidden=list(case.get("forbid_documents", [])),
                        retrieved=sections,
                        documents=[passage.document_title for passage in result.passages],
                    )
                )
    finally:
        pipeline_module._exact_matches = original  # type: ignore[assignment]

    return results


def _summary(results: list[CaseResult]) -> dict[str, Any]:
    scored = [r for r in results if not r.is_isolation_case]
    leakage = [r for r in results if r.is_isolation_case]

    recall = sum(1 for r in scored if r.hit_rank is not None) / len(scored) if scored else 0.0
    mrr = sum(1 / r.hit_rank for r in scored if r.hit_rank) / len(scored) if scored else 0.0
    clean = sum(1 for r in leakage if r.passed) / len(leakage) if leakage else 1.0

    return {"recall": recall, "mrr": mrr, "isolation": clean}


def _print(label: str, results: list[CaseResult], verbose: bool) -> None:
    summary = _summary(results)
    print(
        f"{label:<22} recall@k {summary['recall']:>4.0%}   "
        f"MRR {summary['mrr']:.3f}   role isolation {summary['isolation']:>4.0%}"
    )
    if not verbose:
        return

    for result in results:
        if result.passed:
            continue
        if result.is_isolation_case:
            saw = result.leaked or result.documents
            print(f"      LEAK  {result.case_id}: {result.role} saw {saw}")
        else:
            print(f"      MISS  {result.case_id}: {result.question}")
            print(f"            got {result.retrieved}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--compare", action="store_true", help="ablate rerank and the boost")
    parser.add_argument("--verbose", action="store_true", help="list every failing case")
    args = parser.parse_args()

    os.environ.setdefault("DEMO_MODE", "true")

    from app.db.engine import dispose_engine, session_scope
    from app.db.migrate import upgrade_to_head
    from app.rag.corpus import seed_corpus

    await upgrade_to_head()
    async with session_scope() as db:
        await seed_corpus(db)

    cases = json.loads(EVAL_SET.read_text())["cases"]
    print(f"\n{len(cases)} cases, top-k {args.top_k}\n")

    try:
        if args.compare:
            for label, rerank, boost in (
                ("fusion only", "none", False),
                ("+ exact-term boost", "none", True),
                ("+ cross-encoder", "cross-encoder", False),
                ("+ both", "cross-encoder", True),
            ):
                _print(label, await _run(args.top_k, rerank=rerank, boost=boost), args.verbose)
        else:
            _print(
                "current",
                await _run(args.top_k, rerank="cross-encoder", boost=True),
                args.verbose,
            )
    finally:
        await dispose_engine()

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
