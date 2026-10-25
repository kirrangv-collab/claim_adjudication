"""Typed artifacts passed between the five pipeline agents.

Each agent consumes the previous agents' artifacts and emits its own; the
complete set is returned to clients as ``PipelineTrace`` so every decision is
reproducible and auditable end to end.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TruthValue = Literal["TRUE", "FALSE", "UNKNOWN"]
Effect = Literal["COVERED", "NOT_COVERED", "DISCRETIONARY"]
Assertion = Literal["present", "negated", "undocumented", "historical", "family", "hypothetical"]
DraftDecision = Literal["APPROVE", "DENY", "HUMAN_REVIEW"]
Route = Literal["STRAIGHT_THROUGH", "HUMAN_REVIEW_QUEUE"]


# --- Agent 1: Policy Logic -------------------------------------------------

class QuantityConstraint(BaseModel):
    unit: str
    op: Literal["<=", ">=", "<", ">", "=="]
    value: float


class ASTNode(BaseModel):
    """Policy-logic abstract syntax tree node.

    ``PRED`` leaves hold a condition phrase; ``EXCEPT(base, exception)`` and
    ``UNLESS(base, condition)`` mean ``base AND NOT x``; ``ONLY_IF(base,
    condition)`` means ``base AND condition`` and additionally signals
    non-coverage when the base holds but the condition fails. ``TRUE`` is an
    unconditional rule body.
    """

    id: str
    op: Literal["PRED", "AND", "OR", "NOT", "EXCEPT", "UNLESS", "ONLY_IF", "TRUE"]
    text: str | None = None
    children: list[ASTNode] = Field(default_factory=list)
    quantity: QuantityConstraint | None = None


class PolicySectionInfo(BaseModel):
    id: str
    title: str
    banner: Effect | None = None


class PolicyRule(BaseModel):
    id: str
    section: str | None
    effect: Effect
    # sufficient: establishes the effect when its condition holds.
    # necessary: an "X is only covered when C" constraint; C failing denies,
    #            C holding is neutral.
    # residual: a catch-all ("other uses ... not already addressed") that
    #           applies only when no specific rule is applicable.
    modality: Literal["sufficient", "necessary", "residual"]
    subject: str
    subject_is_anaphoric: bool
    condition: ASTNode
    source_text: str
    parse_method: Literal["grammar", "llm_assisted"] = "grammar"
    parse_confidence: float = Field(ge=0, le=1)


class PolicyDefinition(BaseModel):
    term: str
    expansion: ASTNode
    source_text: str


class SkippedClause(BaseModel):
    text: str
    reason: str


class StructuredPolicyRules(BaseModel):
    sections: list[PolicySectionInfo]
    rules: list[PolicyRule]
    definitions: list[PolicyDefinition]
    abbreviations: dict[str, str]
    skipped_clauses: list[SkippedClause]
    llm_assisted_rules: int = 0
    notes: list[str] = Field(default_factory=list)


# --- Agent 2: Clinical Evidence --------------------------------------------

class EncounterSegment(BaseModel):
    text: str
    label: str
    segment_type: Literal["active", "historical", "family", "boilerplate", "duplicate"]
    reason: str


class ConceptMention(BaseModel):
    text: str
    concept_id: str
    label: str
    system: str
    code: str | None
    score: float
    match: Literal["exact", "embedding", "fuzzy"]
    assertion: Assertion
    source: Literal["diagnosis", "service", "evidence", "policy"]
    sentence: str


class QuantityMention(BaseModel):
    value: float
    unit: str
    text: str
    assertion: Assertion
    sentence: str


class ClinicalExtraction(BaseModel):
    active_segments: list[EncounterSegment]
    excluded_segments: list[EncounterSegment]
    mentions: list[ConceptMention]
    quantities: list[QuantityMention]
    icd_codes: list[str]
    embedder: str
    mcr_windows_scanned: int


# --- Agent 3: Knowledge & Evidence Linker ----------------------------------

class GraphPath(BaseModel):
    """Ontology path linking a clinical-evidence concept to a policy concept.

    ``valid_ancestor``: the evidence concept is a descendant of the policy
    concept through KAMEL valid ancestors (specific evidence satisfies a
    general policy condition). ``general_negation``: the evidence *negates* a
    more general concept, which also negates the specific policy concept.
    """

    from_concept: str
    to_concept: str
    relation: Literal["exact", "valid_ancestor", "general_negation", "indication"]
    path: list[str]
    labels: list[str]
    assertion: str | None = None
    evidence_source: str | None = None


class PredicateConcept(BaseModel):
    concept_id: str
    label: str
    # "implied": the effect in "X due to / secondary to Y" — documented Y implies X.
    required: Literal["present", "absent", "implied"]


class PredicateLink(BaseModel):
    predicate_id: str
    rule_id: str
    predicate_text: str
    category: Literal["clinical", "administrative", "quantitative", "unlinked"]
    concepts: list[PredicateConcept]
    evidence_paths: list[GraphPath]


class RuleRelevance(BaseModel):
    rule_id: str
    score: float
    basis: str


class RetrievedPassage(BaseModel):
    text: str
    score: float
    source: Literal["submitted_policy", "reference_corpus"]
    section: str | None


class LinkedEvidence(BaseModel):
    predicate_links: list[PredicateLink]
    rule_relevance: list[RuleRelevance]
    diagnosis_paths: list[GraphPath]
    diagnosis_linked_to_policy: bool
    retrieved_passages: list[RetrievedPassage]
    reference_passages: list[RetrievedPassage]
    related_sections: list[str] = Field(default_factory=list)
    backends: dict[str, str]


# --- Agent 4: Evidence Reconciliation & Adjudication -----------------------

class PredicateEvaluation(BaseModel):
    predicate_id: str
    text: str
    status: TruthValue
    basis: str
    presumed: bool = False
    closed_world: bool = False
    # TRUE only because the requested service itself matches the condition
    # (e.g. "used in conjunction with verteporfin"); not clinical evidence.
    service_only: bool = False


class RuleEvaluation(BaseModel):
    rule_id: str
    effect: Effect
    modality: str
    relevance: float
    status: TruthValue
    only_if_failed: bool
    predicates: list[PredicateEvaluation]
    source_text: str


class Contradiction(BaseModel):
    kind: Literal["evidence_state", "evidence_assertion", "rule_conflict", "request_evidence"]
    description: str


class UncertaintyMetrics(BaseModel):
    relevant_rules: int
    supporting_rules: int
    opposing_rules_true: int
    opposing_rules_unknown: int
    missing_evidence: int
    contradictions: int
    presumed_conditions: int
    closed_world_inferences: int
    diagnosis_supported: bool
    diagnosis_linked_to_policy: bool
    max_service_relevance: float
    evidence_state: str
    max_retrieval_score: float
    min_parse_confidence: float


class DraftOutcome(BaseModel):
    decision: DraftDecision
    reason_code: str
    rationale: str
    rule_evaluations: list[RuleEvaluation]
    contradictions: list[Contradiction]
    missing_evidence: list[str]
    metrics: UncertaintyMetrics


# --- Agent 5: Confidence & Explainability ----------------------------------

class ConfidenceFactor(BaseModel):
    name: str
    value: float
    contribution: float


class FinalOutcome(BaseModel):
    decision: DraftDecision
    draft_decision: DraftDecision
    route: Route
    confidence: float = Field(ge=0, le=1)
    raw_confidence: float = Field(ge=0, le=1)
    calibration: dict
    factors: list[ConfidenceFactor]
    thresholds: dict[str, float]
    abstention_reasons: list[str]
    audit_brief: str
    audit_brief_source: Literal["template", "llm_assisted"]


class PipelineTrace(BaseModel):
    policy_logic: StructuredPolicyRules
    clinical_evidence: ClinicalExtraction
    knowledge_links: LinkedEvidence
    reconciliation: DraftOutcome
    confidence: FinalOutcome
    agent_latency_ms: dict[str, float]


ASTNode.model_rebuild()
