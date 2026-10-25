"""Ontology model: concepts, synonyms and is-a hierarchy (ICD-10-CM subset +
project-defined grouping/finding/procedure/device/drug concepts)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from backend.pipeline.nlp import normalize_phrase

ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "ontology" / "eye_ontology.json"


@dataclass(frozen=True)
class Concept:
    id: str
    label: str
    system: str
    code: str | None
    synonyms: tuple[str, ...]
    parents: tuple[str, ...]
    va_eligible: bool
    treats: tuple[str, ...] = ()


class Ontology:
    def __init__(self, payload: dict):
        self.metadata = payload.get("metadata", {})
        self.concepts: dict[str, Concept] = {}
        for raw in payload["concepts"]:
            concept = Concept(
                id=raw["id"],
                label=raw["label"],
                system=raw["system"],
                code=raw.get("code"),
                synonyms=tuple(raw.get("synonyms", [])),
                parents=tuple(raw.get("parents", [])),
                va_eligible=raw.get("va_eligible", True),
                treats=tuple(raw.get("treats", [])),
            )
            self.concepts[concept.id] = concept
        for concept in self.concepts.values():
            for related in (*concept.parents, *concept.treats):
                if related not in self.concepts:
                    raise ValueError(f"Concept {concept.id} references unknown concept {related}")
        self.terms: list[tuple[str, str]] = []
        self.exact_index: dict[str, str] = {}
        for concept in self.concepts.values():
            if not concept.va_eligible and not concept.synonyms:
                continue
            for term in (concept.label, *concept.synonyms):
                norm = normalize_phrase(term)
                if not norm:
                    continue
                self.terms.append((term, concept.id))
                # First registration wins so a synonym maps to one concept deterministically.
                self.exact_index.setdefault(norm, concept.id)
        self.term_vocabulary = {tok for norm in self.exact_index for tok in norm.split()}

    def get(self, concept_id: str) -> Concept:
        return self.concepts[concept_id]

    def edges(self) -> list[tuple[str, str]]:
        return [(c.id, p) for c in self.concepts.values() for p in c.parents]

    def treats_edges(self) -> list[tuple[str, str]]:
        return [(c.id, t) for c in self.concepts.values() for t in c.treats]

    def indications(self, concept_id: str) -> set[str]:
        """Conditions a procedure/device/drug is indicated for (inherited through is-a)."""
        found = set(self.concepts[concept_id].treats)
        for ancestor in self.ancestors(concept_id):
            found.update(self.concepts[ancestor].treats)
        return found

    def ancestors(self, concept_id: str) -> dict[str, list[str]]:
        """Map each ancestor to the shortest is-a path ``[concept, ..., ancestor]``."""
        paths: dict[str, list[str]] = {}
        frontier = [[concept_id]]
        while frontier:
            next_frontier = []
            for path in frontier:
                for parent in self.concepts[path[-1]].parents:
                    if parent not in paths:
                        paths[parent] = path + [parent]
                        next_frontier.append(path + [parent])
            frontier = next_frontier
        return paths


@lru_cache(maxsize=1)
def load_ontology(path: str | None = None) -> Ontology:
    target = Path(path) if path else ONTOLOGY_PATH
    return Ontology(json.loads(target.read_text(encoding="utf-8")))
