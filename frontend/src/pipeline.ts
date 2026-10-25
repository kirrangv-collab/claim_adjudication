// Types mirroring backend/pipeline/contracts.py and backend/schemas.py.

export type Decision = "APPROVE" | "DENY" | "HUMAN_REVIEW";
export type Route = "STRAIGHT_THROUGH" | "HUMAN_REVIEW_QUEUE";
export type Truth = "TRUE" | "FALSE" | "UNKNOWN";

export type ASTNode = {
  id: string;
  op: "PRED" | "AND" | "OR" | "NOT" | "EXCEPT" | "UNLESS" | "ONLY_IF" | "TRUE";
  text: string | null;
  children: ASTNode[];
  quantity: { unit: string; op: string; value: number } | null;
};

export type PolicyRule = {
  id: string;
  section: string | null;
  effect: "COVERED" | "NOT_COVERED" | "DISCRETIONARY";
  modality: "sufficient" | "necessary" | "residual";
  subject: string;
  subject_is_anaphoric: boolean;
  condition: ASTNode;
  source_text: string;
  parse_method: "grammar" | "llm_assisted";
  parse_confidence: number;
};

export type StructuredPolicyRules = {
  sections: { id: string; title: string; banner: string | null }[];
  rules: PolicyRule[];
  definitions: { term: string; expansion: ASTNode; source_text: string }[];
  abbreviations: Record<string, string>;
  skipped_clauses: { text: string; reason: string }[];
  llm_assisted_rules: number;
  notes: string[];
};

export type ConceptMention = {
  text: string;
  concept_id: string;
  label: string;
  system: string;
  code: string | null;
  score: number;
  match: "exact" | "embedding" | "fuzzy";
  assertion: "present" | "negated" | "undocumented" | "historical" | "family" | "hypothetical";
  source: "diagnosis" | "service" | "evidence" | "policy";
  sentence: string;
};

export type ClinicalExtraction = {
  active_segments: { text: string; label: string; segment_type: string; reason: string }[];
  excluded_segments: { text: string; label: string; segment_type: string; reason: string }[];
  mentions: ConceptMention[];
  quantities: { value: number; unit: string; text: string; assertion: string; sentence: string }[];
  icd_codes: string[];
  embedder: string;
  mcr_windows_scanned: number;
};

export type GraphPath = {
  from_concept: string;
  to_concept: string;
  relation: "exact" | "valid_ancestor" | "general_negation" | "indication";
  path: string[];
  labels: string[];
  assertion: string | null;
  evidence_source: string | null;
};

export type LinkedEvidence = {
  predicate_links: {
    predicate_id: string;
    rule_id: string;
    predicate_text: string;
    category: "clinical" | "administrative" | "quantitative" | "unlinked";
    concepts: { concept_id: string; label: string; required: "present" | "absent" | "implied" }[];
    evidence_paths: GraphPath[];
  }[];
  rule_relevance: { rule_id: string; score: number; basis: string }[];
  diagnosis_paths: GraphPath[];
  diagnosis_linked_to_policy: boolean;
  retrieved_passages: { text: string; score: number; source: string; section: string | null }[];
  reference_passages: { text: string; score: number; source: string; section: string | null }[];
  related_sections: string[];
  backends: Record<string, string>;
};

export type RuleEvaluation = {
  rule_id: string;
  effect: string;
  modality: string;
  relevance: number;
  status: Truth;
  only_if_failed: boolean;
  predicates: { predicate_id: string; text: string; status: Truth; basis: string; presumed: boolean; closed_world: boolean }[];
  source_text: string;
};

export type DraftOutcome = {
  decision: Decision;
  reason_code: string;
  rationale: string;
  rule_evaluations: RuleEvaluation[];
  contradictions: { kind: string; description: string }[];
  missing_evidence: string[];
  metrics: Record<string, number | boolean | string>;
};

export type FinalOutcome = {
  decision: Decision;
  draft_decision: Decision;
  route: Route;
  confidence: number;
  raw_confidence: number;
  calibration: Record<string, unknown>;
  factors: { name: string; value: number; contribution: number }[];
  thresholds: Record<string, number>;
  abstention_reasons: string[];
  audit_brief: string;
  audit_brief_source: "template" | "llm_assisted";
};

export type PipelineTrace = {
  policy_logic: StructuredPolicyRules;
  clinical_evidence: ClinicalExtraction;
  knowledge_links: LinkedEvidence;
  reconciliation: DraftOutcome;
  confidence: FinalOutcome;
  agent_latency_ms: Record<string, number>;
};

export const AGENT_LABELS: Record<string, string> = {
  policy_logic: "Agent 1 · Policy Logic",
  clinical_evidence: "Agent 2 · Clinical Evidence",
  knowledge_linker: "Agent 3 · Knowledge & Evidence Linker",
  reconciliation: "Agent 4 · Reconciliation & Adjudication",
  confidence_explainability: "Agent 5 · Confidence & Explainability",
};

export function label(value: string): string {
  return value.replaceAll("_", " ");
}
