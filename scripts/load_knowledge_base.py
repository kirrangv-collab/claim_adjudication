"""Seed and verify the knowledge layer (Qdrant vector store + Neo4j graph).

The API seeds its configured backends automatically on first use, so this is
optional; it is useful to pre-load a fresh Qdrant/Neo4j deployment and to
check connectivity. Uses the same CLAIMLAB_* settings as the API.

Run from the project root, e.g.:
    $env:CLAIMLAB_VECTOR_BACKEND = "qdrant"; $env:CLAIMLAB_QDRANT_URL = "http://127.0.0.1:6333"
    $env:CLAIMLAB_GRAPH_BACKEND = "neo4j"; $env:CLAIMLAB_NEO4J_URI = "bolt://127.0.0.1:7687"
    $env:CLAIMLAB_NEO4J_PASSWORD = "..."
    python scripts/load_knowledge_base.py

Exits non-zero if a requested backend fell back to the in-process store.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings
from backend.knowledge import build_knowledge_base


def main() -> int:
    settings = get_settings()
    started = time.perf_counter()
    kb = build_knowledge_base(settings)
    elapsed = (time.perf_counter() - started) * 1000
    print(json.dumps({"status": kb.status, "fallbacks": kb.fallbacks, "seconds": round(elapsed / 1000, 2)}, indent=2))
    terms = kb.vectors.count(kb.normalizer.terms_collection)
    corpus = kb.vectors.count(kb.corpus_collection)
    print(f"Vector store: {terms} ontology-term vectors, {corpus} reference-corpus passages.")
    sample = kb.valid_ancestors("ICD10CM:H40.11")
    print("Valid ancestors of H40.11:", " -> ".join(sample.get("ICD10CM:H40", [])))
    print("Sections mentioning glaucoma:", ", ".join(kb.graph.sections_mentioning(["ICD10CM:H40"])) or "none")
    return 1 if kb.fallbacks else 0


if __name__ == "__main__":
    raise SystemExit(main())
