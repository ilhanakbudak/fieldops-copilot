"""A deterministic stand-in, for tests.

This is **not** a semantic model and must never be selected in production. It
hashes token trigrams into a fixed-width vector, which gives it two properties
the test suite needs and nothing else:

- identical text always produces an identical vector, so assertions are stable
- texts that share vocabulary land closer together than texts that do not, so
  "the right chunk ranks first" is a meaningful assertion for obviously-related
  fixtures

What it buys is a suite that runs in a second and needs no network and no 130 MB
download. What it costs is that CI never exercises the real embedder — stated
plainly here rather than left for someone to discover.
"""

from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbeddingProvider:
    name = "hashing"
    model = "hashing-stub"

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _TOKEN.findall(text.lower())

        # Unigrams carry the topic; bigrams keep word order mattering a little,
        # so "brine valve" and "valve brine" are not identical.
        features = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            # Sign from an independent byte, so collisions cancel instead of
            # accumulating into one dominant direction.
            vector[index] += 1.0 if digest[4] & 1 else -1.0

        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
