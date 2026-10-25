"""Agent 3: Knowledge & Evidence Linker (hybrid Graph-RAG).

* Graph side: links every policy predicate concept to clinical-evidence
  concepts through the ontology graph using KAMEL *valid ancestors* (VAs):
  specific evidence (e.g. H40.11 primary open-angle glaucoma) satisfies a
  general policy condition ("glaucoma", H40) when the policy concept is a VA
  of the evidence concept. Over-generic nodes (chapters/blocks/system roots)
  are never valid ancestors. A negated general concept ("no corneal disease")
  negates its specific descendants.
* Vector side: retrieves the submitted-policy passages most similar to the
  claim, plus related passages from the reference CMS corpus held in the
  vector store (Qdrant or in-memory); the graph store also returns corpus
  sections that mention the claim's concepts.
* Scores how relevant each rule is to the requested service (concept
  subsumption + lexical overlap).
"""
from __future__ import annotations

import re

import numpy as np

from backend.knowledge import KnowledgeBase
from backend.pipeline.contracts import (
    ASTNode,
    ClinicalExtraction,
    GraphPath,
    LinkedEvidence,
    PredicateConcept,
    PredicateLink,
    RetrievedPassage,
    RuleRelevance,
    StructuredPolicyRules,
)
from backend.pipeline.nlp import (
    AssertionClassifier,
    clean_text,
    content_tokens,
    split_sentences,
)

RELEVANCE_THRESHOLD = 0.5
_CAUSAL_RE = re.compile(r"\b(?:due to|secondary to|caused by|resulting from)\b", re.IGNORECASE)
_ADMIN_PHRASES = (
    "reasonable and necessary", "reasonable & necessary", "medically necessary", "food and drug administration",
    "food & drug administration", "fda", "physician", "incident to", "prescribed", "furnished", "individual",
    "supply", "ophthalmologist", "opthalmologist", "mac discretion", "approved", "practitioner", "training program",
    "prosthetic device",
)
_ADMIN_TOKENS = {
    "fda", "food", "drug", "administration", "approved", "physician", "incident", "supply", "service",
    "reasonable", "necessary", "medically", "individual", "medicare", "practitioner", "prescribed", "furnished",
    "program", "training", "benefit", "prosthetic", "device", "ophthalmologist", "used", "use", "receive",
    "mac", "discretion", "through", "intravenously", "situation", "case", "when",
}


def predicate_nodes(node: ASTNode) -> list[ASTNode]:
    if node.op == "PRED":
        return [node]
    return [leaf for child in node.children for leaf in predicate_nodes(child)]


def is_administrative(text: str) -> bool:
    low = text.casefold()
    if any(phrase in low for phrase in _ADMIN_PHRASES):
        return True
    tokens = content_tokens(text)
    return bool(tokens) and sum(t in _ADMIN_TOKENS for t in tokens) / len(tokens) >= 0.5


class KnowledgeLinkerAgent:
    name = "knowledge_linker"

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    # -- graph helpers ---------------------------------------------------------

    def _path(self, from_id: str, to_id: str, relation: str, path: list[str], mention=None) -> GraphPath:
        return GraphPath(
            from_concept=from_id, to_concept=to_id, relation=relation, path=path,
            labels=[self.kb.label(c) for c in path],
            assertion=getattr(mention, "assertion", None), evidence_source=getattr(mention, "source", None),
        )

    def link_concept(self, policy_concept: str, evidence_mentions) -> list[GraphPath]:
        paths = []
        for mention in evidence_mentions:
            evidence_concept = mention.concept_id
            if evidence_concept == policy_concept:
                paths.append(self._path(evidence_concept, policy_concept, "exact", [evidence_concept], mention))
                continue
            ancestors = self.kb.valid_ancestors(evidence_concept)
            if policy_concept in ancestors:
                paths.append(self._path(evidence_concept, policy_concept, "valid_ancestor", ancestors[policy_concept], mention))
                continue
            if mention.assertion == "negated":
                policy_ancestors = self.kb.valid_ancestors(policy_concept)
                if evidence_concept in policy_ancestors:
                    paths.append(self._path(
                        evidence_concept, policy_concept, "general_negation",
                        list(reversed(policy_ancestors[evidence_concept])), mention,
                    ))
        return paths

    def _concepts(self, text: str) -> list[tuple[str, str]]:
        found, _ = self.kb.normalizer.find(text)
        classifier = AssertionClassifier(text)
        return [(m.concept_id, classifier.classify(m.start, m.end)) for m in found]

    # -- relevance -------------------------------------------------------------

    def relevance(self, subject: str, service_text: str, service_concepts: set[str]) -> tuple[float, str]:
        if not subject.strip():
            return 0.6, "No explicit subject; assumed to concern the requested service."
        best, basis = 0.0, "No concept or term overlap with the requested service."
        for concept_id, assertion in self._concepts(subject):
            if assertion == "negated":
                continue
            for service_concept in service_concepts:
                if concept_id == service_concept:
                    return 1.0, f"Subject concept {self.kb.label(concept_id)} matches the requested service."
                if concept_id in self.kb.valid_ancestors(service_concept):
                    return 1.0, (f"Requested {self.kb.label(service_concept)} is-a {self.kb.label(concept_id)} "
                                 "(valid ancestor).")
                if service_concept in self.kb.valid_ancestors(concept_id) and best < 0.7:
                    best, basis = 0.7, (f"Rule concerns {self.kb.label(concept_id)}, a more specific form of "
                                        f"{self.kb.label(service_concept)}.")
        request_tokens = set(content_tokens(service_text))
        subject_tokens = set(content_tokens(subject))
        if request_tokens and subject_tokens:
            overlap = len(request_tokens & subject_tokens)
            lexical = max(overlap / len(request_tokens), overlap / len(subject_tokens)) * 0.95
            if lexical > best:
                best, basis = lexical, f"Term overlap with the requested service ({overlap} shared term(s))."
        return round(best, 3), basis

    # -- retrieval -------------------------------------------------------------

    def _retrieve(self, policy_text: str, query: str) -> tuple[list[RetrievedPassage], list[RetrievedPassage]]:
        sentences = [s for s in split_sentences(clean_text(policy_text)) if len(s) >= 25]
        query_vec = self.kb.embedder.encode([query])
        retrieved: list[RetrievedPassage] = []
        if sentences:
            matrix = self.kb.embedder.encode(sentences)
            scores = (matrix @ query_vec[0]).tolist()
            for idx in np.argsort(-np.array(scores))[:5]:
                retrieved.append(RetrievedPassage(
                    text=sentences[idx][:400], score=round(float(scores[idx]), 4),
                    source="submitted_policy", section=None,
                ))
        hits = self.kb.vectors.search_batch(self.kb.corpus_collection, query_vec, top_k=4, min_score=0.1)[0]
        reference = [
            RetrievedPassage(text=h.payload["text"][:400], score=round(h.score, 4), source="reference_corpus",
                             section=h.payload.get("section"))
            for h in hits
        ]
        return retrieved, reference

    # -- agent -----------------------------------------------------------------

    def run(self, diagnosis: str, requested_service: str, policy_text: str,
            policy: StructuredPolicyRules, extraction: ClinicalExtraction) -> LinkedEvidence:
        evidence_mentions = [m for m in extraction.mentions if m.source in {"evidence", "diagnosis", "service"}]
        service_concepts = {m.concept_id for m in extraction.mentions if m.source == "service" and m.assertion == "present"}
        diagnosis_concepts = {m.concept_id for m in extraction.mentions if m.source == "diagnosis" and m.assertion == "present"}

        relevance = []
        for rule in policy.rules:
            score, basis = self.relevance(rule.subject, requested_service, service_concepts)
            relevance.append(RuleRelevance(rule_id=rule.id, score=score, basis=basis))
        relevant = {r.rule_id for r in relevance if r.score >= RELEVANCE_THRESHOLD}

        links = []
        for rule in policy.rules:
            if rule.id not in relevant:
                continue
            for node in predicate_nodes(rule.condition):
                text = re.sub(r"\((?:unilateral|bilateral)[^)]*\)", "", node.text or "")
                concepts: list[PredicateConcept] = []
                seen: set[str] = set()
                # "X due to / secondary to / caused by Y": the effect X is implied by its cause Y.
                causal = _CAUSAL_RE.search(text)
                effect_concepts = {c for c, _ in self._concepts(text[: causal.start()])} if causal else set()
                for concept_id, assertion in self._concepts(text):
                    if concept_id in seen:
                        continue
                    seen.add(concept_id)
                    required = "absent" if assertion == "negated" else (
                        "implied" if concept_id in effect_concepts else "present")
                    concepts.append(PredicateConcept(
                        concept_id=concept_id, label=self.kb.label(concept_id), required=required,
                    ))
                if concepts and all(c.required == "implied" for c in concepts):
                    concepts = [c.model_copy(update={"required": "present"}) for c in concepts]
                if node.quantity is not None:
                    category = "quantitative"
                elif concepts:
                    category = "clinical"
                elif is_administrative(text):
                    category = "administrative"
                else:
                    category = "unlinked"
                paths = [p for c in concepts for p in self.link_concept(c.concept_id, evidence_mentions)]
                links.append(PredicateLink(
                    predicate_id=node.id, rule_id=rule.id, predicate_text=node.text or "",
                    category=category, concepts=concepts, evidence_paths=paths,
                ))

        # Any policy mention links the diagnosis to a provision: "not covered for X"
        # addresses X just as "covered for X" does, so coverage negation is ignored here.
        policy_concepts = set()
        for sentence in split_sentences(clean_text(policy_text)):
            policy_concepts.update(c for c, _ in self._concepts(sentence))
        claimed = [m for m in extraction.mentions if m.source == "diagnosis" and m.assertion == "present"]
        diagnosis_paths = []
        for concept_id in sorted(policy_concepts):
            diagnosis_paths.extend(self.link_concept(concept_id, claimed))
            # A policy naming a more specific form of the claimed diagnosis also
            # addresses it (rule evaluation still enforces the specific condition).
            for mention in claimed:
                ancestors = self.kb.valid_ancestors(concept_id)
                if mention.concept_id in ancestors:
                    diagnosis_paths.append(self._path(
                        concept_id, mention.concept_id, "valid_ancestor", ancestors[mention.concept_id], mention,
                    ))
            # Procedure-centric policy text: "after cataract surgery" indicates cataract.
            for indicated in sorted(self.kb.indications(concept_id)):
                for path in self.link_concept(indicated, claimed):
                    diagnosis_paths.append(path.model_copy(update={
                        "relation": "indication", "to_concept": concept_id,
                        "path": [*path.path, concept_id], "labels": [*path.labels, self.kb.label(concept_id)],
                    }))
        if diagnosis_concepts:
            linked = bool(diagnosis_paths)
        else:
            tokens = set(content_tokens(diagnosis))
            linked = bool(tokens) and tokens <= set(content_tokens(policy_text))

        present_labels = sorted({m.label for m in extraction.mentions if m.assertion == "present"})
        retrieved, reference = self._retrieve(policy_text, f"{diagnosis}. {requested_service}. {' '.join(present_labels)}")
        related = self.kb.graph.sections_mentioning(sorted(diagnosis_concepts | service_concepts))
        return LinkedEvidence(
            predicate_links=links,
            rule_relevance=relevance,
            diagnosis_paths=diagnosis_paths[:12],
            diagnosis_linked_to_policy=linked,
            retrieved_passages=retrieved,
            reference_passages=reference,
            related_sections=related,
            backends={**self.kb.status},
        )
