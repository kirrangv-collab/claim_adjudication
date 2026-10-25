"""Knowledge layer used by the pipeline agents.

``get_knowledge_base()`` builds (once per process) the ontology, embedder,
vector store and graph store selected by settings. Each external backend is
optional: if it is not configured, not installed, or unreachable, the
in-process fallback is used and the reason is recorded in ``status`` so the
active configuration is always disclosed via ``/api/v1/config``.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import Settings, get_settings
from backend.knowledge.embeddings import (
    Embedder,
    HashingCharNgramEmbedder,
    SBioBertEmbedder,
)
from backend.knowledge.graph_store import (
    GraphStore,
    InMemoryGraphStore,
    Neo4jGraphStore,
)
from backend.knowledge.normalizer import TERMS_COLLECTION, ConceptNormalizer
from backend.knowledge.ontology import Ontology, load_ontology
from backend.knowledge.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStore,
)
from backend.pipeline.nlp import clean_text, split_sentences

logger = logging.getLogger(__name__)
CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "processed" / "ncd_eye_policy_sections.json"
CORPUS_COLLECTION = "policy_corpus"


@dataclass
class KnowledgeBase:
    ontology: Ontology
    embedder: Embedder
    vectors: VectorStore
    graph: GraphStore
    normalizer: ConceptNormalizer
    corpus_collection: str
    status: dict[str, str] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)

    def valid_ancestors(self, concept_id: str) -> dict[str, list[str]]:
        return {
            ancestor: path
            for ancestor, path in self.graph.ancestors(concept_id).items()
            if self.ontology.get(ancestor).va_eligible
        }

    def label(self, concept_id: str) -> str:
        return self.ontology.get(concept_id).label

    def indications(self, concept_id: str) -> set[str]:
        return self.graph.indications(concept_id)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:48]


def _build_embedder(settings: Settings, fallbacks: list[str]) -> Embedder:
    if settings.embedder == "sbiobert":
        try:
            return SBioBertEmbedder(settings.sbiobert_model)
        except Exception as exc:  # noqa: BLE001 - any load failure falls back, with disclosure.
            fallbacks.append(f"SBioBERT unavailable ({type(exc).__name__}); using hashing embedder.")
    return HashingCharNgramEmbedder()


def _build_vectors(settings: Settings, fallbacks: list[str]) -> VectorStore:
    if settings.vector_backend == "qdrant":
        if not settings.qdrant_url:
            fallbacks.append("CLAIMLAB_QDRANT_URL not set; using in-memory vector store.")
        else:
            try:
                return QdrantVectorStore(settings.qdrant_url, settings.qdrant_api_key, settings.qdrant_collection_prefix)
            except Exception as exc:  # noqa: BLE001
                fallbacks.append(f"Qdrant unavailable ({type(exc).__name__}); using in-memory vector store.")
    return InMemoryVectorStore()


def _build_graph(settings: Settings, fallbacks: list[str]) -> GraphStore:
    if settings.graph_backend == "neo4j":
        if not settings.neo4j_uri:
            fallbacks.append("CLAIMLAB_NEO4J_URI not set; using in-memory graph store.")
        else:
            try:
                return Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
            except Exception as exc:  # noqa: BLE001
                fallbacks.append(f"Neo4j unavailable ({type(exc).__name__}); using in-memory graph store.")
    return InMemoryGraphStore()


def load_reference_corpus() -> list[dict]:
    """Sentences of the reference CMS policy corpus, tagged with their section."""
    if not CORPUS_PATH.exists():
        return []
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    rows = []
    for section in payload.get("sections", []):
        for i, sentence in enumerate(split_sentences(clean_text(section["policy_text"]))):
            if len(sentence) >= 25:
                rows.append({
                    "key": f"{section['ncd_section']}#{i}",
                    "section": section["ncd_section"],
                    "title": section["title"],
                    "text": sentence,
                })
    return rows


def build_knowledge_base(settings: Settings | None = None) -> KnowledgeBase:
    settings = settings or get_settings()
    fallbacks: list[str] = []
    ontology = load_ontology()
    embedder = _build_embedder(settings, fallbacks)
    vectors = _build_vectors(settings, fallbacks)
    graph = _build_graph(settings, fallbacks)
    slug = _slug(embedder.name)
    terms_collection = f"{TERMS_COLLECTION}__{slug}"
    corpus_collection = f"{CORPUS_COLLECTION}__{slug}"

    terms = ontology.terms
    vectors.upsert(
        terms_collection,
        [f"{concept_id}::{term}" for term, concept_id in terms],
        embedder.encode([term for term, _ in terms]),
        [{"concept_id": concept_id, "term": term} for term, concept_id in terms],
    )
    normalizer = ConceptNormalizer(ontology, embedder, vectors, terms_collection)

    corpus = load_reference_corpus()
    if corpus:
        vectors.upsert(
            corpus_collection,
            [row["key"] for row in corpus],
            embedder.encode([row["text"] for row in corpus]),
            [{k: row[k] for k in ("section", "title", "text")} for row in corpus],
        )
    section_mentions: dict[str, set[str]] = {}
    for row in corpus:
        mentions, _ = normalizer.find(row["text"], use_embeddings=False)
        section_mentions.setdefault(row["section"], set()).update(m.concept_id for m in mentions)
    graph.load(ontology, section_mentions)

    for message in fallbacks:
        logger.warning("knowledge_fallback %s", message)
    return KnowledgeBase(
        ontology=ontology,
        embedder=embedder,
        vectors=vectors,
        graph=graph,
        normalizer=normalizer,
        corpus_collection=corpus_collection,
        status={
            "embedder": embedder.name,
            "vector_store": vectors.name,
            "graph_store": graph.name,
            "ontology": f"{ontology.metadata.get('name', 'ontology')}@{ontology.metadata.get('version', '?')} ({len(ontology.concepts)} concepts)",
            "reference_corpus_passages": str(len(corpus)),
        },
        fallbacks=fallbacks,
    )


_lock = threading.Lock()
_instance: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = build_knowledge_base()
    return _instance


def reset_knowledge_base(instance: KnowledgeBase | None = None) -> None:
    """Replace (or clear) the process-wide knowledge base; used by tests."""
    global _instance
    with _lock:
        _instance = instance
