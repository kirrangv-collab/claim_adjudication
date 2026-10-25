"""Agent 5: Confidence & Explainability Agent.

* Scores the draft decision from Agent 4's uncertainty metrics with a
  transparent logistic model (documented prior weights), then applies Platt
  calibration (``a * logit + b``) whose parameters are fitted by
  ``scripts/calibrate_confidence.py`` on labelled cases and stored in
  ``data/calibration/pipeline_calibration.json``.
* Routes the case: a draft APPROVE/DENY whose calibrated confidence meets the
  decision-specific threshold (APPROVE >= 0.90, DENY >= 0.95 by default) is
  finalised by Straight-Through Processing; everything else goes to the
  Human Review Queue (calibrated abstention), with explicit reasons.
* Writes a one-page natural-language audit brief. When Gemini is configured
  the narrative summary paragraph may be rewritten by the LLM; the rewrite is
  rejected unless it states the final outcome, names no other outcome, and
  cites only codes present in the trace.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from backend.pipeline.contracts import (
    ClinicalExtraction,
    ConfidenceFactor,
    DraftOutcome,
    FinalOutcome,
    LinkedEvidence,
    StructuredPolicyRules,
)

CALIBRATION_PATH = Path(__file__).resolve().parents[2] / "data" / "calibration" / "pipeline_calibration.json"

# Prior weights of the confidence model (log-odds contributions). They encode
# the design intent — more independent supporting rules raise confidence;
# missing evidence, contradictions, presumptions, closed-world inferences,
# weak service linkage, low parse confidence and incomplete evidence lower it.
PRIOR_WEIGHTS = {
    "bias": 2.4,
    "supporting_rules": 0.6,
    "missing_evidence": -1.2,
    "contradictions": -2.5,
    "presumed_conditions": -0.3,
    "closed_world_inferences": -0.12,
    "diagnosis_supported": 0.8,
    "diagnosis_unsupported": -1.0,
    "diagnosis_linked": 0.5,
    "diagnosis_unlinked": -0.8,
    "service_relevance": 1.2,
    "evidence_incomplete": -2.5,
    "evidence_conflicting": -3.0,
    "resolved_rule_conflict": -1.0,
    "opposing_rules_unknown": -1.0,
    "parse_confidence": 1.5,
}


@lru_cache(maxsize=1)
def load_calibration() -> dict:
    if CALIBRATION_PATH.exists():
        return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    return {"method": "identity", "a": 1.0, "b": 0.0, "n": 0,
            "caveat": "No calibration file found; raw model scores are reported."}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def confidence_logit(draft: DraftOutcome) -> tuple[float, list[ConfidenceFactor]]:
    m = draft.metrics
    w = PRIOR_WEIGHTS
    factors = [
        ("bias", 1.0, w["bias"]),
        ("supporting_rules", m.supporting_rules, w["supporting_rules"] * min(m.supporting_rules, 3)),
        ("missing_evidence", m.missing_evidence, w["missing_evidence"] * min(m.missing_evidence, 3)),
        ("contradictions", m.contradictions, w["contradictions"] * min(m.contradictions, 2)),
        ("presumed_conditions", m.presumed_conditions, w["presumed_conditions"] * min(m.presumed_conditions, 3)),
        ("closed_world_inferences", m.closed_world_inferences,
         w["closed_world_inferences"] * min(m.closed_world_inferences, 6)),
        ("diagnosis_supported", float(m.diagnosis_supported),
         w["diagnosis_supported"] if m.diagnosis_supported else w["diagnosis_unsupported"]),
        ("diagnosis_linked_to_policy", float(m.diagnosis_linked_to_policy),
         w["diagnosis_linked"] if m.diagnosis_linked_to_policy else w["diagnosis_unlinked"]),
        ("service_relevance", m.max_service_relevance, w["service_relevance"] * (m.max_service_relevance - 0.75)),
        ("evidence_state", 0.0 if m.evidence_state == "documented" else 1.0,
         {"documented": 0.0, "incomplete": w["evidence_incomplete"], "conflicting": w["evidence_conflicting"]}[m.evidence_state]),
        ("resolved_rule_conflict", m.opposing_rules_true,
         w["resolved_rule_conflict"] if m.opposing_rules_true else 0.0),
        ("opposing_rules_unknown", m.opposing_rules_unknown, w["opposing_rules_unknown"] * min(m.opposing_rules_unknown, 2)),
        ("parse_confidence", m.min_parse_confidence, w["parse_confidence"] * (m.min_parse_confidence - 1.0)),
    ]
    logit = sum(c for _, _, c in factors)
    return logit, [ConfidenceFactor(name=n, value=round(float(v), 4), contribution=round(c, 4)) for n, v, c in factors]


def _rule_line(evaluation, rules_by_id) -> str:
    rule = rules_by_id.get(evaluation.rule_id)
    quote = re.sub(r"\s+", " ", evaluation.source_text)[:220]
    section = f" [{rule.section}]" if rule and rule.section else ""
    return f"- {evaluation.rule_id}{section} {evaluation.effect.replace('_', ' ')} -> {evaluation.status}: \"{quote}\""


def deterministic_summary(final_decision: str, draft: DraftOutcome, route: str, confidence: float) -> str:
    routed = ("finalised by straight-through processing" if route == "STRAIGHT_THROUGH"
              else "routed to the human review queue")
    summary = (f"The pipeline drafted {draft.decision.replace('_', ' ')} ({draft.reason_code.replace('_', ' ')}) "
               f"with calibrated confidence {confidence:.2f}; the case was {routed}, so the recorded outcome is "
               f"{final_decision.replace('_', ' ')}. {draft.rationale}")
    if draft.missing_evidence:
        summary += f" Missing evidence: {'; '.join(draft.missing_evidence[:4])}."
    return summary


def build_audit_brief(case_id, diagnosis, service, evidence_state, policy: StructuredPolicyRules,
                      extraction: ClinicalExtraction, linked: LinkedEvidence, draft: DraftOutcome,
                      outcome: dict, summary: str) -> str:
    rules_by_id = {r.id: r for r in policy.rules}
    deciding_effect = {"APPROVE": "COVERED", "DENY": "NOT_COVERED"}.get(draft.decision)

    def priority(e):
        decisive_rule = e.status == "TRUE" and e.effect == deciding_effect and e.modality != "necessary"
        return (not decisive_rule, not e.only_if_failed, e.status != "TRUE", -e.relevance)

    decisive = sorted(draft.rule_evaluations, key=priority)[:5]
    present = [m for m in extraction.mentions if m.assertion == "present" and m.source in {"diagnosis", "evidence"}]
    negated = [m for m in extraction.mentions if m.assertion == "negated" and m.source in {"diagnosis", "evidence"}]
    undocumented = [m for m in extraction.mentions if m.assertion == "undocumented"]

    def fmt(mentions, show_status: bool = True):
        seen, items = set(), []
        for m in mentions:
            if m.concept_id not in seen:
                seen.add(m.concept_id)
                status = "" if m.assertion == "present" or not show_status else f" [{m.assertion}]"
                items.append(f"{m.label}{f' ({m.code})' if m.code else ''}{status}")
        return ", ".join(items[:8]) or "none"

    paths = []
    for link in linked.predicate_links:
        for path in link.evidence_paths:
            if path.relation != "exact":
                rendered = f"{' -> '.join(path.labels)} ({path.relation.replace('_', ' ')})"
                if rendered not in paths:
                    paths.append(rendered)
    excluded_types = sorted({s.segment_type for s in extraction.excluded_segments})
    threshold = outcome["thresholds"].get(draft.decision)
    lines = [
        "# Claim adjudication audit brief",
        f"Case: {case_id or 'not provided'} | Engine: five-agent evidence-reconciliation pipeline",
        "",
        "## Outcome",
        (f"- Final outcome: {outcome['decision'].replace('_', ' ')} via "
         f"{'straight-through processing' if outcome['route'] == 'STRAIGHT_THROUGH' else 'human review queue'}"),
        f"- Draft decision (Agent 4): {draft.decision.replace('_', ' ')} - {draft.reason_code.replace('_', ' ')}",
        f"- Calibrated confidence (Agent 5): {outcome['confidence']:.3f}"
        + (f" (STP threshold for {draft.decision}: {threshold:.2f})" if threshold is not None else ""),
    ]
    lines += [f"- Review reason: {r}" for r in outcome["abstention_reasons"]]
    lines += [
        "",
        "## Reviewer summary",
        summary,
        "",
        "## Request",
        f"- Diagnosis: {diagnosis} -> {fmt([m for m in extraction.mentions if m.source == 'diagnosis'])}",
        f"- Requested service: {service}",
        f"- Evidence completeness declared: {evidence_state}",
        "",
        "## Applicable policy rules (Agent 1 -> Agent 4)",
        *([_rule_line(e, rules_by_id) for e in decisive] or ["- No policy rule was linked to the requested service."]),
        "",
        "## Clinical evidence (Agent 2)",
        f"- Active encounter sentences: {len(extraction.active_segments)}; excluded: {len(extraction.excluded_segments)}"
        + (f" ({', '.join(excluded_types)})" if excluded_types else ""),
        f"- Documented: {fmt(present, False)}",
        f"- Negated: {fmt(negated, False)}",
        f"- Stated as not documented: {fmt(undocumented, False)}",
        f"- ICD-10-CM codes: {', '.join(extraction.icd_codes) or 'none'}",
        "",
        "## Knowledge links (Agent 3)",
        f"- Diagnosis linked to a policy indication: {'yes' if linked.diagnosis_linked_to_policy else 'no'}",
        *([f"- {p}" for p in paths[:4]] or ["- Only exact concept matches were needed."]),
        f"- Related reference sections: {', '.join(linked.related_sections[:6]) or 'none'}",
        "",
        "## Reconciliation (Agent 4)",
        f"- Contradictions: {'; '.join(c.description for c in draft.contradictions) or 'none'}",
        f"- Missing evidence: {'; '.join(draft.missing_evidence[:6]) or 'none'}",
        "",
        "## Confidence factors (Agent 5)",
        *[f"- {f.name}: {f.contribution:+.2f}" for f in sorted(outcome["factors"], key=lambda f: -abs(f.contribution))[:6]],
        f"- Calibration: {outcome['calibration'].get('method')} (n={outcome['calibration'].get('n', 0)})",
        "",
        "## Limitations",
        ("- Research prototype; not a medical, legal or coverage determination. Ontology and calibration are "
         "small, project-curated research artifacts."),
    ]
    return "\n".join(lines)


class ConfidenceExplainabilityAgent:
    name = "confidence_explainability"

    def __init__(self, stp_enabled: bool, approve_threshold: float, deny_threshold: float):
        self.stp_enabled = stp_enabled
        self.thresholds = {"APPROVE": approve_threshold, "DENY": deny_threshold}

    def run(self, case_id, diagnosis, service, evidence_state, policy, extraction, linked,
            draft: DraftOutcome) -> FinalOutcome:
        logit, factors = confidence_logit(draft)
        calibration = load_calibration()
        raw = _sigmoid(logit)
        confidence = _sigmoid(calibration.get("a", 1.0) * logit + calibration.get("b", 0.0))
        reasons = []
        if draft.decision == "HUMAN_REVIEW":
            reasons.append(f"Draft outcome requires review ({draft.reason_code.replace('_', ' ')}).")
        elif confidence < self.thresholds[draft.decision]:
            reasons.append(
                f"Calibrated confidence {confidence:.3f} is below the {draft.decision} STP threshold "
                f"{self.thresholds[draft.decision]:.2f}."
            )
        if draft.decision != "HUMAN_REVIEW" and evidence_state != "documented":
            reasons.append("Clinical evidence was not declared complete.")
        if draft.decision != "HUMAN_REVIEW" and draft.contradictions:
            reasons.append("Contradictions were detected.")
        if draft.decision == "DENY" and draft.metrics.opposing_rules_true:
            # Safety rule: never auto-deny against a satisfied coverage provision.
            reasons.append("Denial overrides a satisfied coverage provision (more-specific exclusion); "
                           "a reviewer must confirm.")
        if not self.stp_enabled:
            reasons.append("Straight-through processing is disabled by configuration.")
        route = "HUMAN_REVIEW_QUEUE" if reasons else "STRAIGHT_THROUGH"
        decision = draft.decision if route == "STRAIGHT_THROUGH" else "HUMAN_REVIEW"
        summary = deterministic_summary(decision, draft, route, confidence)
        outcome = {
            "decision": decision, "route": route, "confidence": confidence, "thresholds": self.thresholds,
            "abstention_reasons": reasons, "factors": factors, "calibration": calibration,
        }
        brief = build_audit_brief(case_id, diagnosis, service, evidence_state, policy, extraction, linked,
                                  draft, outcome, summary)
        return FinalOutcome(
            decision=decision, draft_decision=draft.decision, route=route,
            confidence=round(confidence, 4), raw_confidence=round(raw, 4),
            calibration={k: calibration.get(k) for k in ("method", "a", "b", "n", "fitted_on", "caveat") if k in calibration},
            factors=factors, thresholds=self.thresholds, abstention_reasons=reasons,
            audit_brief=brief, audit_brief_source="template",
        )


# --- Optional LLM narrative ---------------------------------------------------------

_LLM_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
_LLM_PROMPT = (
    "You write the 'Reviewer summary' paragraph of a health-insurance claim audit brief for a human claims "
    "reviewer. Use ONLY the facts in the brief provided. State the final outcome exactly as given, explain the "
    "decisive policy rules and evidence in plain language, and mention missing or contradictory evidence. "
    "3-6 sentences, no headings, no new facts, no medical advice."
)
_LABELS = {"APPROVE": "approve", "DENY": "deny", "HUMAN_REVIEW": "human review"}


def validate_llm_summary(summary: str, final: FinalOutcome, extraction: ClinicalExtraction) -> str:
    text = re.sub(r"\s+", " ", summary).strip()
    if not 120 <= len(text) <= 1500:
        raise ValueError("summary length out of bounds")
    low = text.casefold().replace("_", " ")
    if _LABELS[final.decision] not in low:
        raise ValueError("summary does not state the final outcome")
    for label, word in _LABELS.items():
        if label not in {final.decision, final.draft_decision} and re.search(rf"\b{word}(?:d|ed)?\b", low):
            raise ValueError(f"summary mentions a different outcome: {word}")
    allowed = set(extraction.icd_codes) | {m.code for m in extraction.mentions if m.code}
    for code in re.findall(r"\b[A-Z]\d{2}(?:\.\d{1,4})?\b", text):
        if code not in allowed:
            raise ValueError(f"summary cites a code not in the trace: {code}")
    return text


async def refine_brief_with_llm(final: FinalOutcome, extraction: ClinicalExtraction) -> FinalOutcome:
    from backend.llm.gemini_client import (
        GeminiCallError,
        GeminiNotConfigured,
        generate_json,
    )

    try:
        summary, _ = await generate_json(
            _LLM_PROMPT, final.audit_brief, _LLM_SCHEMA,
            lambda data: validate_llm_summary(str(data["summary"]), final, extraction),
        )
    except (GeminiCallError, GeminiNotConfigured):
        return final
    head, _, rest = final.audit_brief.partition("## Reviewer summary\n")
    _, _, after = rest.partition("\n\n")
    brief = f"{head}## Reviewer summary\n{summary} (LLM-drafted from the structured trace; validated.)\n\n{after}"
    return final.model_copy(update={"audit_brief": brief, "audit_brief_source": "llm_assisted"})
