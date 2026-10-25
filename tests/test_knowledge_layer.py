"""Knowledge-layer adapter tests.

The Qdrant adapter is exercised for real through qdrant-client's in-process
``:memory:`` mode (same client code path as a server). Neo4j / SBioBERT are
optional dependencies: their fallbacks are always tested, and a live Neo4j
check runs only when ``CLAIMLAB_TEST_NEO4J_URI`` points at a server.
"""
import os

import numpy as np
import pytest

from backend.config import Settings
from backend.knowledge import build_knowledge_base, get_knowledge_base
from backend.knowledge.embeddings import HashingCharNgramEmbedder
from backend.knowledge.ontology import load_ontology
from backend.knowledge.vector_store import InMemoryVectorStore

SENTENCE = ("Fluorescein angiogram confirms an occult subfoveal choroidal neovascular lesion with no classic "
            "component and documented lesion growth.")


def test_ontology_is_well_formed_and_uses_real_icd_codes():
    ontology = load_ontology()
    assert ontology.get("ICD10CM:H40.11").label == "Primary open-angle glaucoma"
    assert ontology.get("ICD10CM:H52.1").code == "H52.1"
    assert "GRP:CATARACT" in ontology.indications("PROC:PHACO")  # inherited via is-a
    for concept in ontology.concepts.values():
        if concept.system == "ICD10CM" and concept.va_eligible:
            assert concept.code and concept.id == f"ICD10CM:{concept.code}"


def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingCharNgramEmbedder()
    first, second = embedder.encode(["corneal ulcer", "corneal ulcers"])
    assert abs(np.linalg.norm(first) - 1.0) < 1e-5
    assert float(first @ second) > 0.8
    assert np.allclose(embedder.encode(["corneal ulcer"])[0], first)


def test_in_memory_vector_store_upsert_is_idempotent():
    store = InMemoryVectorStore()
    vectors = HashingCharNgramEmbedder().encode(["glaucoma", "cataract"])
    store.upsert("c", ["a", "b"], vectors, [{"v": 1}, {"v": 2}])
    store.upsert("c", ["a"], vectors[:1], [{"v": 3}])
    assert store.count("c") == 2
    hit = store.search_batch("c", vectors[:1], top_k=1, min_score=0.5)[0][0]
    assert hit.key == "a" and hit.payload == {"v": 3}


def test_qdrant_adapter_matches_in_memory_results():
    qdrant_kb = build_knowledge_base(Settings(vector_backend="qdrant", qdrant_url=":memory:"))
    assert qdrant_kb.status["vector_store"] == "qdrant::memory:"
    assert not qdrant_kb.fallbacks
    memory_kb = get_knowledge_base()
    qdrant_mentions = [(m.concept_id, m.match) for m in qdrant_kb.normalizer.find(SENTENCE)[0]]
    memory_mentions = [(m.concept_id, m.match) for m in memory_kb.normalizer.find(SENTENCE)[0]]
    assert qdrant_mentions == memory_mentions
    hits = qdrant_kb.vectors.search_batch(
        qdrant_kb.corpus_collection, qdrant_kb.embedder.encode(["vitrectomy retinal detachment"]), 3, 0.1
    )[0]
    assert hits and hits[0].payload["section"] == "80.11"


def test_unreachable_or_missing_backends_fall_back_with_disclosure():
    kb = build_knowledge_base(Settings(
        vector_backend="qdrant", qdrant_url="http://127.0.0.1:9",
        graph_backend="neo4j", neo4j_uri="bolt://127.0.0.1:9", neo4j_password="x",
        embedder="sbiobert", sbiobert_model="not-a-real/model",
    ))
    assert kb.status["vector_store"] == "memory"
    assert kb.status["graph_store"] == "memory"
    assert kb.status["embedder"] == "hashing-char-ngram-v1"
    assert len(kb.fallbacks) == 3
    missing_url = build_knowledge_base(Settings(vector_backend="qdrant", graph_backend="neo4j"))
    assert any("CLAIMLAB_QDRANT_URL" in f for f in missing_url.fallbacks)
    assert any("CLAIMLAB_NEO4J_URI" in f for f in missing_url.fallbacks)


def test_neo4j_adapter_loads_graph_and_parses_traversals(monkeypatch):
    """Exercise the Neo4j adapter's Python logic against a fake driver."""
    import sys
    import types

    executed: list[tuple[str, dict]] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, **params):
            executed.append((query, params))
            if "RETURN a.id AS ancestor" in query:
                return [
                    {"ancestor": "ICD10CM:H40.1", "path": ["ICD10CM:H40.11", "ICD10CM:H40.1"]},
                    {"ancestor": "ICD10CM:H40", "path": ["ICD10CM:H40.11", "ICD10CM:H40.1", "ICD10CM:H40"]},
                    {"ancestor": "ICD10CM:H40-H42", "path": ["ICD10CM:H40.11", "ICD10CM:H40.1", "ICD10CM:H40", "ICD10CM:H40-H42"]},
                ]
            if "[:TREATS]->(d:Concept) RETURN" in query:
                return [{"id": "GRP:CATARACT"}]
            if "RETURN DISTINCT s.id AS section" in query:
                return [{"section": "80.9"}]
            return []

    class FakeDriver:
        def verify_connectivity(self):
            return None

        def session(self):
            return FakeSession()

    fake_module = types.ModuleType("neo4j")
    fake_module.GraphDatabase = types.SimpleNamespace(driver=lambda uri, auth: FakeDriver())
    monkeypatch.setitem(sys.modules, "neo4j", fake_module)

    kb = build_knowledge_base(Settings(graph_backend="neo4j", neo4j_uri="bolt://fake:7687", neo4j_password="pw"))
    assert kb.status["graph_store"] == "neo4j:bolt://fake:7687" and not kb.fallbacks
    ontology = load_ontology()
    is_a = next(params for query, params in executed if "MERGE (c)-[:IS_A]->(p)" in query)
    treats = next(params for query, params in executed if "MERGE (c)-[:TREATS]->(d)" in query)
    mentions = next(params for query, params in executed if "MERGE (s)-[:MENTIONS]->(c)" in query)
    assert len(is_a["rows"]) == len(ontology.edges())
    assert len(treats["rows"]) == len(ontology.treats_edges())
    assert mentions["rows"]
    # Generic block nodes returned by the graph are filtered out of the VAs.
    assert kb.valid_ancestors("ICD10CM:H40.11") == {
        "ICD10CM:H40.1": ["ICD10CM:H40.11", "ICD10CM:H40.1"],
        "ICD10CM:H40": ["ICD10CM:H40.11", "ICD10CM:H40.1", "ICD10CM:H40"],
    }
    assert kb.indications("PROC:PHACO") == {"GRP:CATARACT"}
    assert kb.graph.sections_mentioning(["ICD10CM:H40"]) == ["80.9"]


@pytest.mark.skipif(not os.getenv("CLAIMLAB_TEST_NEO4J_URI"), reason="Set CLAIMLAB_TEST_NEO4J_URI to run against Neo4j.")
def test_live_neo4j_graph_store_traversal():
    kb = build_knowledge_base(Settings(
        graph_backend="neo4j", neo4j_uri=os.environ["CLAIMLAB_TEST_NEO4J_URI"],
        neo4j_user=os.getenv("CLAIMLAB_TEST_NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("CLAIMLAB_TEST_NEO4J_PASSWORD"),
    ))
    assert kb.status["graph_store"].startswith("neo4j:")
    assert kb.valid_ancestors("ICD10CM:H40.11")["ICD10CM:H40"] == ["ICD10CM:H40.11", "ICD10CM:H40.1", "ICD10CM:H40"]
    assert "GRP:CATARACT" in kb.indications("PROC:PHACO")
    assert "80.9" in kb.graph.sections_mentioning(["ICD10CM:H40.11"])
