"""Turning text into vectors for policy search. Swappable, like the LLM.

    hashing    (default) offline and deterministic: character n-grams hashed into 384 dimensions.
               Needs no model download. It is LEXICAL: it matches shared word pieces ("arrive" ~
               "arrived"), not meaning ("parcel" ~ "order"). The retrieval eval shows the difference.
    fastembed  a real semantic model (BAAI/bge-small-en-v1.5, 384 dims, runs locally on CPU).
               Needs the optional `fastembed` package and a one-time model download.

Select with CASEGUARD_EMBEDDER=hashing|fastembed.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

DIM = 384


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class HashingEmbedder:
    name, dim = "hashing", DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            padded = f"<{word}>"
            grams = [word] + [padded[i:i + n] for n in (3, 4, 5) for i in range(len(padded) - n + 1)]
            for g in grams:
                h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
                v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0       # signed hashing trick
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


class FastEmbedEmbedder:
    name, dim = "fastembed", DIM

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding          # optional dependency: pip install ".[semantic]"
        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(texts)]


def make_embedder(name: str | None = None) -> Embedder:
    name = name or os.getenv("CASEGUARD_EMBEDDER", "hashing")
    if name == "hashing":
        return HashingEmbedder()
    if name == "fastembed":
        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedder: {name}")


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))          # vectors are already L2-normalised
