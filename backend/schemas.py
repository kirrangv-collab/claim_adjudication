from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["APPROVE", "DENY", "HUMAN_REVIEW"]


class InferenceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str | None = Field(default=None, max_length=100)
    diagnosis: str = Field(min_length=1, max_length=500)
    requested_service: str = Field(min_length=1, max_length=500)
    policy_text: str = Field(min_length=1, max_length=20_000)
    clinical_evidence: str = Field(min_length=1, max_length=10_000)
    evidence_state: Literal["documented", "incomplete", "conflicting"] = "documented"


class AgentFinding(BaseModel):
    agent: Literal["policy_interpreter", "evidence_assessor", "reconciler"]
    status: Literal["supports", "opposes", "uncertain", "not_applicable"]
    summary: str
    evidence: list[str] = Field(default_factory=list)


class VerifiedQuote(BaseModel):
    quote: str
    verified_in_policy_text: bool


class LLMEngineResult(BaseModel):
    """Secondary, comparison-only signal from an LLM/RAG engine.

    This is never the authoritative decision. ``decision`` is null when the
    engine could not produce a usable result after bounded retries; in that
    case ``error`` discloses why, rather than silently defaulting to any
    particular decision.
    """

    engine: Literal["llm_rag"]
    model: str
    decision: Decision | None
    rationale: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    supporting_quotes: list[VerifiedQuote]
    attempts: int
    latency_ms: float = Field(ge=0)
    error: str | None


class AdjudicationResult(BaseModel):
    case_id: str | None
    decision: Decision
    rationale: str
    confidence: float = Field(ge=0, le=1)
    confidence_note: str
    findings: list[AgentFinding]
    limitations: list[str]
    latency_ms: float = Field(ge=0)
    llm_result: LLMEngineResult | None = None


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inference_case: InferenceCase
    ground_truth: Decision


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvaluationCase] = Field(min_length=1, max_length=500)


class ClassMetrics(BaseModel):
    precision: float
    recall: float
    f1: float
    support: int


class EvaluationResponse(BaseModel):
    model: str
    total_cases: int
    evaluated_cases: int
    errored_cases: int
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    false_approval_rate: float
    false_denial_rate: float
    human_review_rate: float
    average_latency_ms: float
    per_class: dict[str, ClassMetrics]
    confusion_matrix: dict[str, dict[str, int]]
    case_results: list["EvaluationCaseResult"]
    errors: list[str]


class EvaluationCaseResult(BaseModel):
    case_index: int
    case_id: str | None
    ground_truth: Decision
    prediction: Decision | None
    status: Literal["evaluated", "error"]
    latency_ms: float | None
    error: str | None


class SampleCasesResponse(BaseModel):
    cases: list[EvaluationCase]
    provenance: dict | None
    is_real_world_policy_text: Literal[True]
    is_synthetic_clinical_evidence: Literal[True]
    ground_truth_caveat: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    role: str


class UserPublic(BaseModel):
    username: str
    role: str
    is_active: bool


HumanDecision = Literal["APPROVED", "DENIED", "ESCALATED"]


class CaseReviewSummary(BaseModel):
    id: int
    case_id: str | None
    suggested_decision: Decision
    suggested_confidence: float
    llm_decision: Decision | None = None
    status: Literal["pending_review", "reviewed"]
    created_by: str
    created_at: datetime
    human_decision: HumanDecision | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class CaseReviewDetail(CaseReviewSummary):
    inference_case: InferenceCase
    suggested_rationale: str
    findings: list[AgentFinding]
    limitations: list[str]
    human_notes: str | None
    llm_result: LLMEngineResult | None = None


class RecordDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_decision: HumanDecision
    human_notes: str = Field(default="", max_length=4000)


class CaseReviewListResponse(BaseModel):
    items: list[CaseReviewSummary]
    total: int
    page: int
    page_size: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    prototype: Literal[True]
    benchmark_available: Literal[False]


class ConfigResponse(BaseModel):
    model: str
    inference_type: Literal["deterministic_rules"]
    llm_enabled: bool
    llm_provider: str | None = None
    llm_analysis_model: str | None = None
    llm_endpoint_host: str | None = None
    llm_role: str | None = None
    environment: str
    auth_required: Literal[True]
    audit_trail_enabled: Literal[True]
    production_ready: Literal[False]
    clinically_validated: Literal[False]
    available_models: list[str]
    unavailable_baselines: dict[str, str]
    supported_decisions: list[Decision]
    benchmark_available: Literal[False]
