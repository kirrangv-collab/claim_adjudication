"""Orchestrates the five-agent adjudication pipeline.

Incoming claim & policy -> Agent 1 Policy Logic -> Agent 2 Clinical Evidence
-> Agent 3 Knowledge & Evidence Linker -> Agent 4 Reconciliation &
Adjudication -> Agent 5 Confidence & Explainability -> Straight-Through
Processing (APPROVE/DENY) or Human Review Queue.

``run_pipeline`` is fully deterministic (used for evaluation and tests).
``run_pipeline_async`` additionally lets Gemini assist Agent 1 and Agent 5 when
configured; LLM output is validated and can never change the decision logic
(it only affects clause parsing that is itself validated, and the narrative).
"""
from __future__ import annotations

import time

from backend.config import Settings, get_settings
from backend.knowledge import KnowledgeBase, get_knowledge_base
from backend.pipeline.clinical_evidence import ClinicalEvidenceAgent
from backend.pipeline.confidence import (
    ConfidenceExplainabilityAgent,
    refine_brief_with_llm,
)
from backend.pipeline.contracts import PipelineTrace, StructuredPolicyRules
from backend.pipeline.knowledge_linker import KnowledgeLinkerAgent
from backend.pipeline.policy_logic import PolicyLogicAgent, refine_with_llm
from backend.pipeline.reconciliation import ReconciliationAgent
from backend.schemas import (
    AdjudicationResult,
    AgentFinding,
    BaselineResult,
    InferenceCase,
)

MODEL_ID = "multi_agent_pipeline"
PIPELINE_VERSION = "1.0.0"
AGENTS = [
    ("policy_logic", "Agent 1: Policy Logic Agent"),
    ("clinical_evidence", "Agent 2: Clinical Evidence Agent"),
    ("knowledge_linker", "Agent 3: Knowledge & Evidence Linker"),
    ("reconciliation", "Agent 4: Evidence Reconciliation & Adjudication Agent"),
    ("confidence_explainability", "Agent 5: Confidence & Explainability Agent"),
]
LIMITATIONS = [
    "Research prototype: not a medical, legal or coverage determination; never use with real patient data.",
    ("Policy parsing is a rule-based grammar tuned to CMS NCD language; unusual phrasing can be mis-parsed "
     "(low-confidence parses are disclosed per rule)."),
    ("The ontology is a small curated ICD-10-CM subset plus project-defined concepts; unseen terminology is "
     "only matched when the embedder finds a close synonym."),
    ("Confidence comes from a transparent logistic model over the agents' uncertainty metrics, with optional "
     "Platt calibration on a small project-authored labelled sample; treat it as an indicative score, not a "
     "validated probability."),
    ("Administrative conditions (FDA approval, 'reasonable and necessary') are presumed met and disclosed as "
     "presumptions."),
]


def _stage(timings: dict, name: str, started: float) -> float:
    now = time.perf_counter()
    timings[name] = round((now - started) * 1000, 3)
    return now


class Pipeline:
    def __init__(self, kb: KnowledgeBase | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.kb = kb or get_knowledge_base()
        self.evidence_agent = ClinicalEvidenceAgent(self.kb)
        self.linker_agent = KnowledgeLinkerAgent(self.kb)
        self.reconciliation_agent = ReconciliationAgent(self.kb)
        self.confidence_agent = ConfidenceExplainabilityAgent(
            self.settings.stp_enabled, self.settings.stp_approve_threshold, self.settings.stp_deny_threshold,
        )

    def _after_policy(self, case: InferenceCase, policy: StructuredPolicyRules, timings: dict, started: float):
        extraction = self.evidence_agent.run(case.diagnosis, case.requested_service, case.clinical_evidence)
        started = _stage(timings, "clinical_evidence", started)
        linked = self.linker_agent.run(case.diagnosis, case.requested_service, case.policy_text, policy, extraction)
        started = _stage(timings, "knowledge_linker", started)
        draft = self.reconciliation_agent.run(case.diagnosis, case.evidence_state, policy, extraction, linked)
        started = _stage(timings, "reconciliation", started)
        final = self.confidence_agent.run(case.case_id, case.diagnosis, case.requested_service, case.evidence_state,
                                          policy, extraction, linked, draft)
        _stage(timings, "confidence_explainability", started)
        return PipelineTrace(policy_logic=policy, clinical_evidence=extraction, knowledge_links=linked,
                             reconciliation=draft, confidence=final, agent_latency_ms=timings)

    def run(self, case: InferenceCase) -> PipelineTrace:
        timings: dict[str, float] = {}
        started = time.perf_counter()
        policy = PolicyLogicAgent().run(case.policy_text)  # per-run state; agent is cheap
        started = _stage(timings, "policy_logic", started)
        return self._after_policy(case, policy, timings, started)

    async def run_async(self, case: InferenceCase, llm_assist: bool) -> PipelineTrace:
        timings: dict[str, float] = {}
        started = time.perf_counter()
        policy = PolicyLogicAgent().run(case.policy_text)  # per-run state; agent is cheap
        if llm_assist:
            policy = await refine_with_llm(policy)
        started = _stage(timings, "policy_logic", started)
        trace = self._after_policy(case, policy, timings, started)
        if llm_assist:
            started = time.perf_counter()
            final = await refine_brief_with_llm(trace.confidence, trace.clinical_evidence)
            timings["confidence_explainability"] += round((time.perf_counter() - started) * 1000, 3)
            trace = trace.model_copy(update={"confidence": final, "agent_latency_ms": timings})
        return trace


_pipeline: Pipeline | None = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


def reset_pipeline() -> None:
    global _pipeline
    _pipeline = None


def _findings(trace: PipelineTrace) -> list[AgentFinding]:
    policy, extraction, linked = trace.policy_logic, trace.clinical_evidence, trace.knowledge_links
    draft, final = trace.reconciliation, trace.confidence
    relevant = [r for r in linked.rule_relevance if r.score >= 0.5]
    present = sorted({m.label for m in extraction.mentions if m.assertion == "present" and m.source != "service"})
    negated = sorted({m.label for m in extraction.mentions if m.assertion == "negated"})
    non_exact = [p for link in linked.predicate_links for p in link.evidence_paths if p.relation != "exact"]
    return [
        AgentFinding(
            agent="policy_logic", status="supports" if policy.rules else "uncertain",
            summary=(f"Parsed {len(policy.rules)} rule(s) and {len(policy.definitions)} definition(s) from "
                     f"{len(policy.sections) or 1} section(s); {policy.llm_assisted_rules} LLM-assisted."),
            evidence=[f"{r.id}:{r.effect}" for r in policy.rules[:8]],
        ),
        AgentFinding(
            agent="clinical_evidence", status="supports" if present else "uncertain",
            summary=(f"{len(extraction.active_segments)} active sentence(s), {len(extraction.excluded_segments)} "
                     f"excluded; {len(present)} documented and {len(negated)} negated concept(s)."),
            evidence=extraction.icd_codes[:8] or present[:8],
        ),
        AgentFinding(
            agent="knowledge_linker", status="supports" if linked.diagnosis_linked_to_policy else "uncertain",
            summary=(f"{len(relevant)} rule(s) relevant to the service; {len(non_exact)} valid-ancestor/negation "
                     f"path(s); diagnosis {'is' if linked.diagnosis_linked_to_policy else 'is not'} linked to a policy indication."),
            evidence=[" -> ".join(p.labels) for p in non_exact[:4]],
        ),
        AgentFinding(
            agent="reconciliation",
            status={"APPROVE": "supports", "DENY": "opposes"}.get(draft.decision, "uncertain"),
            summary=draft.rationale,
            evidence=draft.missing_evidence[:6] + [c.description for c in draft.contradictions][:4],
        ),
        AgentFinding(
            agent="confidence_explainability",
            status="supports" if final.route == "STRAIGHT_THROUGH" else "uncertain",
            summary=(f"Calibrated confidence {final.confidence:.3f}; route "
                     f"{final.route.replace('_', ' ').lower()}." + (f" {final.abstention_reasons[0]}" if final.abstention_reasons else "")),
            evidence=[],
        ),
    ]


def to_result(case: InferenceCase, trace: PipelineTrace, baseline: AdjudicationResult | None = None,
              latency_ms: float | None = None) -> AdjudicationResult:
    final = trace.confidence
    calibration = final.calibration
    return AdjudicationResult(
        case_id=case.case_id,
        decision=final.decision,
        draft_decision=final.draft_decision,
        route=final.route,
        rationale=trace.reconciliation.rationale,
        confidence=final.confidence,
        confidence_note=(
            f"Calibrated ({calibration.get('method', 'identity')}, n={calibration.get('n', 0)}) score from the "
            "pipeline's uncertainty metrics; indicative, not a validated probability."
        ),
        findings=_findings(trace),
        limitations=LIMITATIONS,
        latency_ms=latency_ms if latency_ms is not None else sum(trace.agent_latency_ms.values()),
        audit_brief=final.audit_brief,
        pipeline=trace,
        baseline=BaselineResult(model="b1_lexical", decision=baseline.decision,
                                rationale=baseline.rationale) if baseline else None,
    )


def adjudicate_pipeline(case: InferenceCase) -> AdjudicationResult:
    """Deterministic entry point (evaluation, tests)."""
    from backend.agents import adjudicate as baseline_adjudicate

    started = time.perf_counter()
    trace = get_pipeline().run(case)
    return to_result(case, trace, baseline_adjudicate(case), (time.perf_counter() - started) * 1000)


async def adjudicate_pipeline_async(case: InferenceCase, llm_assist: bool) -> AdjudicationResult:
    from backend.agents import adjudicate as baseline_adjudicate

    started = time.perf_counter()
    trace = await get_pipeline().run_async(case, llm_assist=llm_assist)
    return to_result(case, trace, baseline_adjudicate(case), (time.perf_counter() - started) * 1000)
