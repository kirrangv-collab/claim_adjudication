"""Graph-store adapters: in-memory and Neo4j.

The graph holds the ontology's is-a hierarchy plus ``(PolicySection)-[:MENTIONS]->
(Concept)`` edges built from the reference policy corpus. Agent 3 uses it for
KAMEL valid-ancestor traversal and to find policy sections related to a
claim's concepts.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from backend.knowledge.ontology import Ontology


class GraphStore(Protocol):
    name: str

    def load(self, ontology: Ontology, section_mentions: dict[str, set[str]]) -> None: ...

    def ancestors(self, concept_id: str) -> dict[str, list[str]]: ...

    def sections_mentioning(self, concept_ids: list[str]) -> list[str]: ...

    def indications(self, concept_id: str) -> set[str]: ...


class InMemoryGraphStore:
    name = "memory"

    def __init__(self):
        self._ontology: Ontology | None = None
        self._mentions: dict[str, set[str]] = {}
        self._ancestor_cache: dict[str, dict[str, list[str]]] = {}

    def load(self, ontology, section_mentions):
        self._ontology = ontology
        self._ancestor_cache.clear()
        self._mentions = {}
        for section, concepts in section_mentions.items():
            for concept_id in concepts:
                self._mentions.setdefault(concept_id, set()).add(section)

    def ancestors(self, concept_id):
        if concept_id not in self._ancestor_cache:
            self._ancestor_cache[concept_id] = self._ontology.ancestors(concept_id)
        return self._ancestor_cache[concept_id]

    def sections_mentioning(self, concept_ids):
        sections: set[str] = set()
        for concept_id in concept_ids:
            sections |= self._mentions.get(concept_id, set())
            for ancestor in self.ancestors(concept_id):
                if self._ontology.get(ancestor).va_eligible:
                    sections |= self._mentions.get(ancestor, set())
        return sorted(sections)

    def indications(self, concept_id):
        return self._ontology.indications(concept_id)


class Neo4jGraphStore:
    """Neo4j adapter (``CLAIMLAB_GRAPH_BACKEND=neo4j``).

    Concepts are stored as ``(:Concept {id, label, system, code, va_eligible})``
    nodes with ``[:IS_A]`` edges; corpus sections as ``(:PolicySection {id})``
    with ``[:MENTIONS]`` edges. Loading is idempotent (``MERGE``).
    """

    def __init__(self, uri: str, user: str, password: str | None):
        from neo4j import GraphDatabase  # optional dependency

        self._driver = GraphDatabase.driver(uri, auth=(user, password or ""))
        self._driver.verify_connectivity()
        self.name = f"neo4j:{uri}"

    def _run(self, query: str, **params):
        with self._driver.session() as session:
            return list(session.run(query, **params))

    def load(self, ontology, section_mentions):
        self._run("CREATE CONSTRAINT concept_id IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE")
        self._run("CREATE CONSTRAINT section_id IF NOT EXISTS FOR (s:PolicySection) REQUIRE s.id IS UNIQUE")
        self._run(
            "UNWIND $rows AS row MERGE (c:Concept {id: row.id}) "
            "SET c.label = row.label, c.system = row.system, c.code = row.code, c.va_eligible = row.va_eligible",
            rows=[
                {"id": c.id, "label": c.label, "system": c.system, "code": c.code, "va_eligible": c.va_eligible}
                for c in ontology.concepts.values()
            ],
        )
        self._run(
            "UNWIND $rows AS row MATCH (c:Concept {id: row.child}), (p:Concept {id: row.parent}) "
            "MERGE (c)-[:IS_A]->(p)",
            rows=[{"child": child, "parent": parent} for child, parent in ontology.edges()],
        )
        self._run(
            "UNWIND $rows AS row MATCH (c:Concept {id: row.source}), (d:Concept {id: row.target}) "
            "MERGE (c)-[:TREATS]->(d)",
            rows=[{"source": source, "target": target} for source, target in ontology.treats_edges()],
        )
        self._run(
            "UNWIND $rows AS row MERGE (s:PolicySection {id: row.section}) "
            "WITH s, row MATCH (c:Concept {id: row.concept}) MERGE (s)-[:MENTIONS]->(c)",
            rows=[
                {"section": section, "concept": concept}
                for section, concepts in section_mentions.items()
                for concept in concepts
            ],
        )
        self._ancestors_cached.cache_clear()
        self._indications_cached.cache_clear()

    @lru_cache(maxsize=4096)  # noqa: B019 - store instances live for the process lifetime.
    def _indications_cached(self, concept_id: str) -> frozenset[str]:
        records = self._run(
            "MATCH (c:Concept {id: $id})-[:IS_A*0..10]->(:Concept)-[:TREATS]->(d:Concept) RETURN DISTINCT d.id AS id",
            id=concept_id,
        )
        return frozenset(record["id"] for record in records)

    def indications(self, concept_id):
        return set(self._indications_cached(concept_id))

    @lru_cache(maxsize=4096)  # noqa: B019 - store instances live for the process lifetime.
    def _ancestors_cached(self, concept_id: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        records = self._run(
            "MATCH p = (c:Concept {id: $id})-[:IS_A*1..10]->(a:Concept) "
            "RETURN a.id AS ancestor, [n IN nodes(p) | n.id] AS path ORDER BY length(p)",
            id=concept_id,
        )
        seen: dict[str, tuple[str, ...]] = {}
        for record in records:
            seen.setdefault(record["ancestor"], tuple(record["path"]))
        return tuple(seen.items())

    def ancestors(self, concept_id):
        return {ancestor: list(path) for ancestor, path in self._ancestors_cached(concept_id)}

    def sections_mentioning(self, concept_ids):
        records = self._run(
            "MATCH (c:Concept) WHERE c.id IN $ids "
            "OPTIONAL MATCH (c)-[:IS_A*0..10]->(a:Concept) WHERE a = c OR a.va_eligible "
            "MATCH (s:PolicySection)-[:MENTIONS]->(a) RETURN DISTINCT s.id AS section ORDER BY section",
            ids=list(concept_ids),
        )
        return [record["section"] for record in records]
