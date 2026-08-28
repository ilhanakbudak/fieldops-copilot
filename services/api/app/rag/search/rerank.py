"""Reranking.

Retrieval and ranking are different problems, and the pipeline is better for
treating them that way. Fusion is cheap and works on ranks; it has no idea what
the passages *say*. A cross-encoder reads the question and one passage together
and scores that pair directly — which is far more accurate and far too expensive
to run over a corpus, so it runs over the forty candidates fusion produced.

This is usually the largest single quality gain in a RAG pipeline, and it is the
step most often skipped because the naive version already returns something.

Three implementations behind one protocol:

- `CrossEncoderReranker` — a local ONNX model. No API, no per-query cost, and
  the corpus never leaves the deployment.
- `LexicalReranker` — deterministic term overlap. Used by the test suite, so the
  assertions are about the pipeline rather than about a model's mood, and as the
  fallback when the model cannot be loaded.
- `NoopReranker` — trust fusion alone.

A hosted reranker (Cohere, Voyage) would be a fourth class here. It is not
implemented because it would be untestable in a repository that must run with no
credentials, and the interface makes clear it would be a drop-in.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Protocol, runtime_checkable

from app.config import Settings

logger = logging.getLogger("fieldops.rag.rerank")

MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"

_WORD = re.compile(r"[a-z0-9][a-z0-9\-/.]*")


@runtime_checkable
class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class NoopReranker:
    name = "none"

    def score(self, query: str, passages: list[str]) -> list[float]:
        # Descending, so the caller's stable sort preserves the fusion order.
        return [float(len(passages) - index) for index in range(len(passages))]


class LexicalReranker:
    """Term overlap, length-normalised.

    Not a good reranker — it cannot tell that "warm water" and "elevated
    temperature" are the same idea, which is most of what a cross-encoder is
    for. It is a *deterministic* one, which is what a test suite needs.
    """

    name = "lexical"

    def score(self, query: str, passages: list[str]) -> list[float]:
        wanted = set(_WORD.findall(query.lower()))
        if not wanted:
            return [0.0] * len(passages)

        scores: list[float] = []
        for passage in passages:
            tokens = set(_WORD.findall(passage.lower()))
            overlap = wanted & tokens
            # The square root damps the advantage of a long passage that
            # contains every word by accident.
            scores.append(len(overlap) / (len(tokens) ** 0.5 or 1.0))
        return scores


class CrossEncoderReranker:
    """`ms-marco-MiniLM-L-6-v2` through FastEmbed.

    Downloads ~90 MB on first use and then runs on CPU in a few tens of
    milliseconds for forty passages. Loaded lazily so a deployment that never
    asks a question never pays for it.
    """

    name = "cross-encoder"

    def __init__(self) -> None:
        self._model: object | None = None
        self._lock = threading.Lock()
        self._failed = False

    def _encoder(self) -> object | None:
        if self._model is None and not self._failed:
            with self._lock:
                if self._model is None and not self._failed:
                    try:
                        from fastembed.rerank.cross_encoder import TextCrossEncoder

                        logger.info("loading reranker %s (first run downloads ~90 MB)", MODEL)
                        self._model = TextCrossEncoder(model_name=MODEL)
                    except Exception:
                        # A missing model must degrade the ranking, not fail the
                        # question. Logged once, then the lexical fallback takes
                        # over for the life of the process.
                        logger.exception("reranker unavailable; falling back to lexical")
                        self._failed = True
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []

        encoder = self._encoder()
        if encoder is None:
            return LexicalReranker().score(query, passages)

        return [float(value) for value in encoder.rerank(query, passages)]  # type: ignore[attr-defined]


def build_reranker(settings: Settings) -> Reranker:
    if settings.rerank_provider == "cross-encoder":
        return CrossEncoderReranker()
    if settings.rerank_provider == "lexical":
        return LexicalReranker()
    return NoopReranker()


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    """One per process: the model is ~90 MB of weights."""
    global _reranker
    if _reranker is None:
        from app.config import get_settings

        _reranker = build_reranker(get_settings())
    return _reranker


def reset_reranker() -> None:
    global _reranker
    _reranker = None
