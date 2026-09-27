import re
import time

from backend.schemas import AdjudicationResult, AgentFinding, InferenceCase

MODEL_ID = "evidence_reconciliation_prototype"
LIMITATIONS = [
    "This deterministic research prototype is not a medical or coverage determination.",
    "Text matching is lexical only; it does not establish clinical equivalence or interpret full policy context.",
    "Confidence is a rule-based heuristic, not a calibrated probability.",
    "The frozen research benchmark and original B1/B2/B3 implementations are not present in this workspace.",
]

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "the", "to", "with", "not", "no",
}
_EXCLUSION_MARKERS = (
    "not covered",
    "not eligible",
    "not medically necessary",
    "not payable",
    "is excluded",
    "are excluded",
    "does not cover",
)
_COVERAGE_MARKERS = ("covered", "eligible", "medically necessary", "is payable")
_POLICY_QUALIFIERS = ("except", "unless", "only if")
_EVIDENCE_NEGATION_MARKERS = (
    "no evidence",
    "not documented",
    "without evidence",
    "evidence is absent",
    "not present",
    "ruled out",
    "denies",
)


def _terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def adjudicate(case: InferenceCase) -> AdjudicationResult:
    started = time.perf_counter()
    policy_text = case.policy_text.casefold()
    positive_policy_text = policy_text
    for marker in _EXCLUSION_MARKERS:
        positive_policy_text = positive_policy_text.replace(marker, " ")
    request_terms = _terms(f"{case.diagnosis} {case.requested_service}")
    policy_terms = _terms(case.policy_text)
    evidence_terms = _terms(case.clinical_evidence)
    policy_overlap = sorted(request_terms & policy_terms)
    evidence_overlap = sorted(request_terms & evidence_terms)

    exclusion_marker = next(
        (marker for marker in _EXCLUSION_MARKERS if marker in policy_text), None
    )
    coverage_marker = next(
        (marker for marker in _COVERAGE_MARKERS if marker in positive_policy_text), None
    )
    qualified_policy = any(marker in policy_text for marker in _POLICY_QUALIFIERS)
    policy_conflict = bool(
        exclusion_marker and coverage_marker and policy_overlap
    )
    exclusion_applies = bool(
        exclusion_marker and policy_overlap and not qualified_policy and not policy_conflict
    )
    evidence_negated = any(
        marker in case.clinical_evidence.casefold()
        for marker in _EVIDENCE_NEGATION_MARKERS
    )
    evidence_sufficient = (
        bool(evidence_overlap)
        and case.evidence_state == "documented"
        and not evidence_negated
    )

    policy_status = (
        "uncertain" if qualified_policy or policy_conflict else
        "opposes" if exclusion_applies else
        "supports" if coverage_marker and policy_overlap else
        "uncertain"
    )
    evidence_status = (
        "supports" if evidence_sufficient else
        "opposes" if case.evidence_state == "conflicting" else
        "uncertain"
    )
    policy_finding = AgentFinding(
        agent="policy_interpreter",
        status=policy_status,
        summary=(
            "Policy contains qualifiers or the limited lexical check could not establish "
            "an applicable rule."
            if qualified_policy or policy_conflict else
            f"Found exclusion marker '{exclusion_marker}' with a request-term match."
            if exclusion_applies else
            f"Found coverage marker '{coverage_marker}' and a request-term match."
            if policy_status == "supports" else
            "Could not establish an applicable policy rule using the limited lexical check."
        ),
        evidence=policy_overlap[:8],
    )
    evidence_finding = AgentFinding(
        agent="evidence_assessor",
        status=evidence_status,
        summary=(
            "Documented evidence contains terms matching the request."
            if evidence_sufficient else
            "Evidence contains negation or absence language and cannot be treated as support."
            if evidence_negated else
            "Evidence is marked conflicting."
            if case.evidence_state == "conflicting" else
            "Sufficient matching evidence was not established."
        ),
        evidence=evidence_overlap[:8],
    )

    if qualified_policy or policy_conflict:
        decision = "HUMAN_REVIEW"
        rationale = (
            "Policy conditions or conflicting coverage/exclusion language require "
            "full-context human review."
        )
        confidence = 0.9
    elif evidence_negated:
        decision = "HUMAN_REVIEW"
        rationale = "Evidence contains negation or absence language that requires human review."
        confidence = 0.9
    elif case.evidence_state == "conflicting":
        decision = "HUMAN_REVIEW"
        rationale = "Conflicting evidence requires human reconciliation."
        confidence = 0.9
    elif exclusion_applies:
        decision = "DENY"
        rationale = "A policy exclusion marker and matching request terms were detected."
        confidence = 0.8
    elif policy_status == "supports" and evidence_sufficient:
        decision = "APPROVE"
        rationale = "A coverage marker and documented matching evidence were detected."
        confidence = 0.75
    else:
        decision = "HUMAN_REVIEW"
        rationale = (
            "Policy applicability or supporting evidence remains uncertain; "
            "human review is the conservative outcome."
        )
        confidence = 0.65

    reconciler_finding = AgentFinding(
        agent="reconciler",
        status="supports" if decision != "HUMAN_REVIEW" else "uncertain",
        summary=rationale,
        evidence=[],
    )
    return AdjudicationResult(
        case_id=case.case_id,
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        confidence_note="Rule-based heuristic; not a calibrated probability.",
        findings=[policy_finding, evidence_finding, reconciler_finding],
        limitations=LIMITATIONS,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
