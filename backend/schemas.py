from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline.contracts import PipelineTrace

Decision = Literal["APPROVE", "DENY", "HUMAN_REVIEW"]
Route = Literal["STRAIGHT_THROUGH", "HUMAN_REVIEW_QUEUE"]
EngineName = Literal["multi_agent_pipeline", "b1_lexical", "llm_rag"]


class InferenceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str | None = Field(default=None, max_length=100)
    diagnosis: str = Field(min_length=1, max_length=500)
    requested_service: str = Field(min_length=1, max_length=500)
    policy_text: str = Field(min_length=1, max_length=20_000)
    clinical_evidence: str = Field(min_length=1, max_length=10_000)
    evidence_state: Literal["documented", "incomplete", "conflicting"] = "documented"


class AgentFinding(BaseModel):
    agent: Literal[
        "policy_logic", "clinical_evidence", "knowledge_linker", "reconciliation", "confidence_explainability",
        # B1 lexical baseline stages
        "policy_interpreter", "evidence_assessor", "reconciler",
    ]
    status: Literal["supports", "opposes", "uncertain", "not_applicable"]
    summary: str
    evidence: list[str] = Field(default_factory=list)


class BaselineResult(BaseModel):
    """B1 lexical baseline decision, returned alongside the pipeline for comparison."""

    model: str
    decision: Decision
    rationale: str


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
    # Final outcome: APPROVE/DENY only when finalised by straight-through
    # processing; HUMAN_REVIEW when routed to the human review queue.
    decision: Decision
    draft_decision: Decision | None = None
    route: Route | None = None
    rationale: str
    confidence: float = Field(ge=0, le=1)
    confidence_note: str
    findings: list[AgentFinding]
    limitations: list[str]
    latency_ms: float = Field(ge=0)
    audit_brief: str | None = None
    pipeline: PipelineTrace | None = None
    baseline: BaselineResult | None = None
    llm_result: LLMEngineResult | None = None


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inference_case: InferenceCase
    ground_truth: Decision


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvaluationCase] = Field(min_length=1, max_length=500)
    engine: EngineName = "multi_agent_pipeline"


class EvaluationCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvaluationCase] = Field(min_length=1, max_length=500)
    include_llm: bool = False


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
    # Multi-agent pipeline only (null for engines without routing).
    stp_rate: float | None = None
    stp_accuracy: float | None = None
    draft_accuracy: float | None = None


class EvaluationCaseResult(BaseModel):
    case_index: int
    case_id: str | None
    ground_truth: Decision
    prediction: Decision | None
    status: Literal["evaluated", "error"]
    latency_ms: float | None
    error: str | None
    draft_decision: Decision | None = None
    route: Route | None = None
    confidence: float | None = None


class EvaluationComparisonResponse(BaseModel):
    engines: dict[str, EvaluationResponse]
    disagreements: list[dict]
    notes: list[str]


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
ReviewStatus = Literal["pending_review", "reviewed", "auto_decided"]


class CaseReviewSummary(BaseModel):
    id: int
    case_id: str | None
    # Agent 4 draft decision and Agent 5 calibrated confidence (advisory).
    suggested_decision: Decision
    suggested_confidence: float
    llm_decision: Decision | None = None
    route: Route | None = None
    status: ReviewStatus
    # Authoritative outcome: set by straight-through processing (decision_source
    # "stp") or by a human reviewer ("human"); null while pending/escalated.
    final_decision: Decision | None = None
    decision_source: Literal["stp", "human"] | None = None
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
    audit_brief: str | None = None
    pipeline: PipelineTrace | None = None
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
    pipeline_ready: bool = True
    knowledge_backends: dict[str, str] | None = None


class PipelineAgentInfo(BaseModel):
    id: str
    name: str


class PipelineConfig(BaseModel):
    version: str
    agents: list[PipelineAgentInfo]
    stp_enabled: bool
    stp_thresholds: dict[str, float]
    llm_assist_enabled: bool
    knowledge_backends: dict[str, str]
    knowledge_fallbacks: list[str]
    calibration: dict


class PublicDemoAccount(BaseModel):
    username: str
    role: str


class PublicDemoInfo(BaseModel):
    """Only present when the server runs as a public demo; the password is intentionally public."""

    accounts: list[PublicDemoAccount]
    password: str
    notice: str


class ConfigResponse(BaseModel):
    model: str
    inference_type: Literal["multi_agent_pipeline"]
    pipeline: PipelineConfig
    public_demo: PublicDemoInfo | None = None
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
