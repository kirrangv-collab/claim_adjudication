"""Text embedders used for concept normalisation and passage retrieval.

``HashingCharNgramEmbedder`` is the dependency-free default: signed feature
hashing of character 3/4-grams. It is deterministic and fast but only captures
surface (spelling/morphology) similarity — it is *not* a semantic model.
``SBioBertEmbedder`` uses a sentence-transformers SBioBERT model when the
optional ``sbiobert`` extra is installed and ``CLAIMLAB_EMBEDDER=sbiobert``.
"""
from __future__ import annotations

import zlib
from typing import Protocol

import numpy as np

from backend.pipeline.nlp import normalize_phrase


class Embedder(Protocol):
    name: str
    dim: int
    match_threshold: float

    def encode(self, texts: list[str]) -> np.ndarray: ...


class HashingCharNgramEmbedder:
    name = "hashing-char-ngram-v1"

    def __init__(self, dim: int = 1024, match_threshold: float = 0.86):
        self.dim = dim
        self.match_threshold = match_threshold

        self._cache: dict[str, np.ndarray] = {}

    def _vector(self, text: str) -> np.ndarray:
        padded = f" {normalize_phrase(text)} "
        if padded in self._cache:
            return self._cache[padded]
        hashes = [
            zlib.crc32(padded[i:i + n].encode("utf-8"))
            for n in (3, 4)
            for i in range(len(padded) - n + 1)
        ]
        vec = np.bincount(
            [h % self.dim for h in hashes],
            weights=[1.0 if (h >> 31) & 1 else -1.0 for h in hashes],
            minlength=self.dim,
        ).astype(np.float32)
        norm = float(np.linalg.norm(vec))
        vec = vec / norm if norm else vec
        if len(self._cache) < 200_000:
            self._cache[padded] = vec
        return vec

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self._vector(t) for t in texts])


class SBioBertEmbedder:
    def __init__(self, model_name: str, match_threshold: float = 0.80):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self._model = SentenceTransformer(model_name)
        self.name = f"sbiobert:{model_name}"
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.match_threshold = match_threshold

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return vectors.astype(np.float32)
