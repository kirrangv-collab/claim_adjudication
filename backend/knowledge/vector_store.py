"""Vector-store adapters: in-memory (numpy) and Qdrant.

Both expose the same small interface so Agents 2 and 3 never depend on which
backend is active. Vectors are expected to be L2-normalised, so cosine
similarity equals the dot product.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class VectorHit:
    key: str
    score: float
    payload: dict


class VectorStore(Protocol):
    name: str

    def upsert(self, collection: str, keys: list[str], vectors: np.ndarray, payloads: list[dict]) -> None: ...

    def search_batch(
        self, collection: str, vectors: np.ndarray, top_k: int, min_score: float
    ) -> list[list[VectorHit]]: ...

    def count(self, collection: str) -> int: ...


class InMemoryVectorStore:
    name = "memory"

    def __init__(self):
        self._collections: dict[str, tuple[list[str], np.ndarray, list[dict]]] = {}

    def upsert(self, collection, keys, vectors, payloads):
        existing_keys, existing_vectors, existing_payloads = self._collections.get(
            collection, ([], np.zeros((0, vectors.shape[1]), dtype=np.float32), [])
        )
        index = {k: i for i, k in enumerate(existing_keys)}
        merged_keys = list(existing_keys)
        merged_payloads = list(existing_payloads)
        rows = [existing_vectors]
        for key, vector, payload in zip(keys, vectors, payloads):
            if key in index:
                existing_vectors[index[key]] = vector
                merged_payloads[index[key]] = payload
            else:
                merged_keys.append(key)
                merged_payloads.append(payload)
                rows.append(vector[None, :])
        self._collections[collection] = (merged_keys, np.vstack(rows).astype(np.float32), merged_payloads)

    def search_batch(self, collection, vectors, top_k, min_score):
        if collection not in self._collections or len(vectors) == 0:
            return [[] for _ in range(len(vectors))]
        keys, matrix, payloads = self._collections[collection]
        scores = vectors @ matrix.T
        results = []
        for row in scores:
            order = np.argsort(-row)[:top_k]
            results.append([
                VectorHit(keys[i], float(row[i]), payloads[i]) for i in order if row[i] >= min_score
            ])
        return results

    def count(self, collection):
        return len(self._collections.get(collection, ([], None, []))[0])


class QdrantVectorStore:
    """Qdrant adapter (``CLAIMLAB_VECTOR_BACKEND=qdrant``).

    ``url=":memory:"`` uses qdrant-client's in-process local mode, which runs
    the real client code path without a server (used by the adapter tests).
    """

    def __init__(self, url: str, api_key: str | None = None, prefix: str = "claimlab"):
        from qdrant_client import QdrantClient, models  # optional dependency

        self._models = models
        self._client = (
            QdrantClient(location=":memory:") if url == ":memory:"
            else QdrantClient(url=url, api_key=api_key, timeout=10, check_compatibility=False)
        )
        self._prefix = prefix
        self.name = f"qdrant:{url}"
        # Fail fast if the server is unreachable so the factory can fall back.
        self._client.get_collections()

    def _name(self, collection: str) -> str:
        return f"{self._prefix}_{collection}"

    @staticmethod
    def _point_id(key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    def upsert(self, collection, keys, vectors, payloads):
        models = self._models
        name = self._name(collection)
        if not self._client.collection_exists(name):
            self._client.create_collection(
                name,
                vectors_config=models.VectorParams(size=int(vectors.shape[1]), distance=models.Distance.COSINE),
            )
        batch = 256
        for start in range(0, len(keys), batch):
            self._client.upsert(
                name,
                points=[
                    models.PointStruct(id=self._point_id(k), vector=v.tolist(), payload={**p, "_key": k})
                    for k, v, p in zip(keys[start:start + batch], vectors[start:start + batch], payloads[start:start + batch])
                ],
            )

    def search_batch(self, collection, vectors, top_k, min_score):
        name = self._name(collection)
        if len(vectors) == 0 or not self._client.collection_exists(name):
            return [[] for _ in range(len(vectors))]
        models = self._models
        responses = self._client.query_batch_points(
            name,
            requests=[
                models.QueryRequest(query=v.tolist(), limit=top_k, score_threshold=min_score, with_payload=True)
                for v in vectors
            ],
        )
        results = []
        for response in responses:
            hits = []
            for point in response.points:
                payload = dict(point.payload or {})
                key = payload.pop("_key", str(point.id))
                hits.append(VectorHit(key, float(point.score), payload))
            results.append(hits)
        return results

    def count(self, collection):
        name = self._name(collection)
        if not self._client.collection_exists(name):
            return 0
        return int(self._client.count(name).count)
