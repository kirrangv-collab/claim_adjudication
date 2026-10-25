"""Agent 4: Evidence Reconciliation & Adjudication Agent.

Evaluates every relevant rule's AST against the linked clinical evidence with
Kleene three-valued logic (TRUE / FALSE / UNKNOWN):

* Clinical predicates are satisfied through Agent 3's graph paths (exact or
  KAMEL valid-ancestor matches); negated evidence (directly or via a negated
  general concept) falsifies them; "not documented" evidence leaves them
  UNKNOWN. When the submitter declares the record complete
  (``evidence_state=documented``) a concept that is absent is treated as
  FALSE (closed-world assumption); otherwise it is UNKNOWN.
* Quantitative predicates compare documented quantities (never closed-world).
* Administrative predicates (FDA approval, "reasonable and necessary",
  physician supply) are presumed satisfied and counted as presumptions.

It then reconciles the rule outcomes into a draft decision, detecting
contradictions (conflicting evidence, request vs. evidence, rule conflicts)
and missing evidence, and emits uncertainty metrics for Agent 5.
"""
from __future__ import annotations

from backend.knowledge import KnowledgeBase
from backend.pipeline.contracts import (
    ASTNode,
    ClinicalExtraction,
    Contradiction,
    DraftOutcome,
    LinkedEvidence,
    PredicateEvaluation,
    RuleEvaluation,
    StructuredPolicyRules,
    UncertaintyMetrics,
)
from backend.pipeline.knowledge_linker import RELEVANCE_THRESHOLD
from backend.pipeline.nlp import STOP_WORDS, compare, content_tokens, normalize_phrase

T, F, U = "TRUE", "FALSE", "UNKNOWN"
_GENERIC_TOKENS = STOP_WORDS | {
    "certain", "case", "cases", "condition", "conditions", "specific", "cause", "causes", "other", "type", "types",
    # Purpose/process words: "evaluation of X" is verified by X itself.
    "evaluation", "treatment", "diagnosis", "management", "assessment", "correction", "purpose", "use",
    "documented", "presence", "history",
}


def _and(values):
    values = list(values)
    if F in values:
        return F
    return T if all(v == T for v in values) else U


def _or(values):
    values = list(values)
    if T in values:
        return T
    return F if values and all(v == F for v in values) else U


def _not(value):
    return {T: F, F: T, U: U}[value]


class ReconciliationAgent:
    name = "reconciliation"

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    # -- predicate evaluation ----------------------------------------------------

    def _evaluate_predicate(self, node, link, extraction, closed_world, evidence_text) -> PredicateEvaluation:
        text = node.text or ""
        if node.quantity is not None:
            q = node.quantity
            for found in extraction.quantities:
                if found.unit == q.unit and found.assertion == "present":
                    ok = compare(found.value, q.op, q.value)
                    return PredicateEvaluation(
                        predicate_id=node.id, text=text, status=T if ok else F,
                        basis=f"Documented {found.value:g} {found.unit} {'satisfies' if ok else 'violates'} {q.op} {q.value:g}.",
                    )
            return PredicateEvaluation(predicate_id=node.id, text=text, status=U,
                                       basis=f"No documented measurement in {q.unit} units.")
        if link is None or link.category == "unlinked":
            tokens = [t for t in content_tokens(text) if t not in _GENERIC_TOKENS]
            if not tokens:
                return PredicateEvaluation(predicate_id=node.id, text=text, status=U, basis="Condition is too generic to verify.")
            found = sum(t in evidence_text for t in tokens) / len(tokens)
            if found >= 0.75:
                return PredicateEvaluation(predicate_id=node.id, text=text, status=T,
                                           basis="Condition terms appear in the active clinical evidence (lexical).")
            return PredicateEvaluation(predicate_id=node.id, text=text, status=U,
                                       basis="Could not link condition to an ontology concept or the evidence.")
        if link.category == "administrative":
            return PredicateEvaluation(predicate_id=node.id, text=text, status=T, presumed=True,
                                       basis="Administrative condition presumed met (provider attestation).")
        statuses, bases, closed = [], [], False
        implied: list[tuple[str, bool, bool]] = []
        service_supported: list[bool] = []
        for concept in link.concepts:
            paths = [p for p in link.evidence_paths if p.to_concept == concept.concept_id]
            linked_paths = [p for p in paths if p.relation in {"exact", "valid_ancestor"}]
            assertions = {p.assertion for p in linked_paths}
            general_negated = any(p.relation == "general_negation" for p in paths)
            positive = "present" in assertions
            negative = "negated" in assertions or general_negated
            undocumented = "undocumented" in assertions
            # A past or possible finding is real but unconfirmed information: it is
            # never treated as absent under the closed-world assumption.
            uncertain = bool(assertions & {"historical", "hypothetical"})
            if positive:
                service_supported.append(all(
                    p.evidence_source == "service" for p in linked_paths if p.assertion == "present"))
            if concept.required == "implied":
                implied.append((concept.label, positive, negative))
                continue
            if concept.required == "present":
                if positive:
                    status = T
                    via = next(p for p in paths if p.assertion == "present")
                    bases.append(f"{concept.label}: documented" + (
                        f" via {' -> '.join(via.labels)}" if via.relation == "valid_ancestor" else ""))
                elif negative:
                    status = F
                    bases.append(f"{concept.label}: negated in evidence")
                elif undocumented:
                    status = U
                    bases.append(f"{concept.label}: stated as not documented")
                elif uncertain:
                    status = U
                    bases.append(f"{concept.label}: only historical or possible, not confirmed in this encounter")
                elif closed_world:
                    status, closed = F, True
                    bases.append(f"{concept.label}: absent from complete record (closed-world)")
                else:
                    status = U
                    bases.append(f"{concept.label}: not documented")
            else:
                if positive:
                    status = F
                    bases.append(f"{concept.label}: present but must be absent")
                elif negative:
                    status = T
                    bases.append(f"{concept.label}: documented as absent")
                elif undocumented:
                    status = U
                    bases.append(f"{concept.label}: absence not documented")
                elif uncertain:
                    status = U
                    bases.append(f"{concept.label}: historical or possible — absence cannot be assumed")
                elif closed_world:
                    status, closed = T, True
                    bases.append(f"{concept.label}: absent from complete record (closed-world)")
                else:
                    status = U
                    bases.append(f"{concept.label}: absence not documented")
            statuses.append(status)
        cause_status = _and(statuses)
        for label, positive, negative in implied:
            if positive:
                statuses.append(T)
                bases.append(f"{label}: documented")
            elif negative:
                statuses.append(F)
                bases.append(f"{label}: negated in evidence")
            else:
                statuses.append(cause_status)
                bases.append(f"{label}: implied by its documented cause" if cause_status == T
                             else f"{label}: depends on its cause")
        status = _and(statuses)
        return PredicateEvaluation(predicate_id=node.id, text=text, status=status,
                                   basis="; ".join(bases), closed_world=closed,
                                   service_only=status == T and bool(service_supported) and all(service_supported))

    def _evaluate(self, node: ASTNode, ctx, results: dict[str, PredicateEvaluation]) -> str:
        if node.op == "TRUE":
            return T
        if node.op == "PRED":
            evaluation = self._evaluate_predicate(node, ctx["links"].get(node.id), ctx["extraction"],
                                                  ctx["closed_world"], ctx["evidence_text"])
            results[node.id] = evaluation
            return evaluation.status
        values = [self._evaluate(child, ctx, results) for child in node.children]
        if node.op == "AND":
            return _and(values)
        if node.op == "OR":
            return _or(values)
        if node.op == "NOT":
            return _not(values[0])
        if node.op in {"EXCEPT", "UNLESS"}:
            return _and([values[0], _not(values[1])])
        if node.op == "ONLY_IF":
            return _and(values)
        raise ValueError(f"Unknown AST operator {node.op}")

    # -- reconciliation ------------------------------------------------------------

    def run(self, diagnosis: str, evidence_state: str, policy: StructuredPolicyRules,
            extraction: ClinicalExtraction, linked: LinkedEvidence) -> DraftOutcome:
        closed_world = evidence_state == "documented"
        relevance = {r.rule_id: r.score for r in linked.rule_relevance}
        ctx = {
            "links": {link.predicate_id: link for link in linked.predicate_links},
            "extraction": extraction,
            "closed_world": closed_world,
            "evidence_text": " " + " ".join(normalize_phrase(s.text) for s in extraction.active_segments) + " ",
        }
        evaluations: list[RuleEvaluation] = []
        specificity: dict[str, int] = {}
        for rule in policy.rules:
            score = relevance.get(rule.id, 0.0)
            if score < RELEVANCE_THRESHOLD:
                continue
            results: dict[str, PredicateEvaluation] = {}
            status = self._evaluate(rule.condition, ctx, results)
            only_if_failed = False
            if rule.condition.op == "ONLY_IF":
                base = self._evaluate(rule.condition.children[0], ctx, {})
                cond = self._evaluate(rule.condition.children[1], ctx, {})
                only_if_failed = base == T and cond == F
            specificity[rule.id] = sum(1 for r in results.values() if r.status == T and not r.presumed)
            evaluations.append(RuleEvaluation(
                rule_id=rule.id, effect=rule.effect, modality=rule.modality, relevance=score, status=status,
                only_if_failed=only_if_failed, predicates=list(results.values()), source_text=rule.source_text,
            ))

        contradictions = self._contradictions(evidence_state, extraction)
        rules_by_id = {r.id: r for r in policy.rules}
        decision, reason, rationale, conflict_resolved = self._decide(
            evaluations, specificity, contradictions, extraction, linked, rules_by_id,
        )
        missing = []
        for evaluation in evaluations:
            if evaluation.status == U and evaluation.modality != "residual":
                for predicate in evaluation.predicates:
                    if predicate.status == U and predicate.text not in missing:
                        missing.append(predicate.text)

        supporting_effect = {"APPROVE": "COVERED", "DENY": "NOT_COVERED"}.get(decision)
        has_sufficient_coverage = any(e.effect == "COVERED" and e.modality == "sufficient" for e in evaluations)
        supporting = [e for e in evaluations if supporting_effect and (
            (e.status == T and e.effect == supporting_effect
             and (e.modality != "necessary" or not has_sufficient_coverage))
            or (decision == "DENY" and e.only_if_failed))]
        opposing_effect = {"APPROVE": "NOT_COVERED", "DENY": "COVERED"}.get(decision)
        opposing_true = [e for e in evaluations if opposing_effect and e.effect == opposing_effect and e.status == T
                         and e.modality != "necessary"]
        opposing_unknown = [e for e in evaluations if opposing_effect and e.effect == opposing_effect and e.status == U
                            and e.modality != "residual"]
        all_predicates = [p for e in evaluations for p in e.predicates]
        metrics = UncertaintyMetrics(
            relevant_rules=len(evaluations),
            supporting_rules=len(supporting),
            opposing_rules_true=len(opposing_true) if conflict_resolved else 0,
            opposing_rules_unknown=len(opposing_unknown),
            missing_evidence=len(missing),
            contradictions=len(contradictions),
            presumed_conditions=len({p.text for e in supporting for p in e.predicates if p.presumed}),
            closed_world_inferences=sum(1 for p in all_predicates if p.closed_world),
            diagnosis_supported=self._diagnosis_supported(diagnosis, extraction),
            diagnosis_linked_to_policy=linked.diagnosis_linked_to_policy,
            max_service_relevance=max((e.relevance for e in evaluations), default=0.0),
            evidence_state=evidence_state,
            max_retrieval_score=max((p.score for p in linked.retrieved_passages), default=0.0),
            min_parse_confidence=min((rules_by_id[e.rule_id].parse_confidence for e in supporting), default=1.0),
        )
        return DraftOutcome(
            decision=decision, reason_code=reason, rationale=rationale, rule_evaluations=evaluations,
            contradictions=contradictions, missing_evidence=missing[:12], metrics=metrics,
        )

    def _contradictions(self, evidence_state, extraction) -> list[Contradiction]:
        found = []
        if evidence_state == "conflicting":
            found.append(Contradiction(kind="evidence_state", description="Submitter marked the clinical evidence as conflicting."))
        by_concept: dict[str, set[str]] = {}
        for mention in extraction.mentions:
            if mention.source == "evidence":
                by_concept.setdefault(mention.concept_id, set()).add(mention.assertion)
        for concept_id, assertions in by_concept.items():
            if {"present", "negated"} <= assertions:
                found.append(Contradiction(
                    kind="evidence_assertion",
                    description=f"{self.kb.label(concept_id)} is both affirmed and negated in the active encounter.",
                ))
        for mention in extraction.mentions:
            if mention.source != "diagnosis" or mention.assertion != "present":
                continue
            related = {mention.concept_id, *self.kb.valid_ancestors(mention.concept_id)}
            if any(m.source == "evidence" and m.assertion == "negated" and m.concept_id in related for m in extraction.mentions):
                found.append(Contradiction(
                    kind="request_evidence",
                    description=f"Claimed diagnosis {mention.label} is negated by the clinical evidence.",
                ))
        return found

    def _diagnosis_supported(self, diagnosis, extraction) -> bool:
        claimed = [m for m in extraction.mentions if m.source == "diagnosis" and m.assertion == "present"]
        evidence = {m.concept_id for m in extraction.mentions if m.source == "evidence" and m.assertion == "present"}
        # A documented procedure/device supports the condition it is indicated for.
        evidence |= {indicated for concept_id in list(evidence) for indicated in self.kb.indications(concept_id)}
        if claimed:
            for d in claimed:
                for e in evidence:
                    if e == d.concept_id or d.concept_id in self.kb.valid_ancestors(e) \
                            or e in self.kb.valid_ancestors(d.concept_id):
                        return True
            return False
        tokens = set(content_tokens(diagnosis))
        evidence_tokens = {t for s in extraction.active_segments for t in content_tokens(s.text)}
        return bool(tokens) and len(tokens & evidence_tokens) / len(tokens) >= 0.5

    def _decide(self, evaluations, specificity, contradictions, extraction, linked, rules_by_id):
        sufficient = [e for e in evaluations if e.modality == "sufficient"]
        cov_t = [e for e in sufficient if e.effect == "COVERED" and e.status == T]
        cov_u = [e for e in sufficient if e.effect == "COVERED" and e.status == U]
        if not any(e.effect == "COVERED" for e in sufficient):
            # With no separate coverage rule, "X is covered only when C" is the
            # whole coverage criterion: C is necessary *and* sufficient.
            necessary = [e for e in evaluations if e.effect == "COVERED" and e.modality == "necessary"]
            cov_t = [e for e in necessary if e.status == T]
            cov_u = [e for e in necessary if e.status == U]
        exc_t = [e for e in sufficient if e.effect == "NOT_COVERED" and e.status == T]
        exc_u = [e for e in sufficient if e.effect == "NOT_COVERED" and e.status == U]
        only_if_failed = [e for e in evaluations if e.effect == "COVERED" and e.only_if_failed]
        disc_t = [e for e in evaluations if e.effect == "DISCRETIONARY" and e.status == T]
        residual_t = [e for e in evaluations if e.modality == "residual" and e.status == T]
        if not (cov_t or cov_u or exc_t or exc_u or only_if_failed or disc_t):
            exc_t = residual_t

        def ids(items):
            return ", ".join(e.rule_id for e in items)

        if contradictions:
            return "HUMAN_REVIEW", "contradiction", (
                "Contradictory evidence detected: " + "; ".join(c.description for c in contradictions)
            ), False
        deny_side = exc_t + only_if_failed
        if cov_t and deny_side:
            approve_spec = max(specificity.get(e.rule_id, 0) for e in cov_t)
            deny_spec = max(specificity.get(e.rule_id, 0) for e in deny_side)
            if approve_spec > deny_spec:
                return "APPROVE", "specific_coverage_overrides_exclusion", (
                    f"Coverage rule(s) {ids(cov_t)} are satisfied and are more specific than the conflicting "
                    f"exclusion(s) {ids(deny_side)}; the specific provision governs."
                ), True
            if deny_spec > approve_spec:
                return "DENY", "specific_exclusion_overrides_coverage", (
                    f"Exclusion(s) {ids(deny_side)} are satisfied and are more specific than coverage rule(s) "
                    f"{ids(cov_t)}; the specific provision governs."
                ), True
            return "HUMAN_REVIEW", "rule_conflict", (
                f"Coverage rule(s) {ids(cov_t)} and exclusion(s) {ids(deny_side)} are both satisfied with equal "
                "specificity; a reviewer must resolve the conflict."
            ), False
        if deny_side:
            if cov_u:
                return "HUMAN_REVIEW", "exception_undetermined", (
                    f"Exclusion(s) {ids(deny_side)} apply, but coverage provision(s) {ids(cov_u)} could still apply "
                    "and depend on evidence that is missing."
                ), False
            reason = "only_if_condition_failed" if only_if_failed and not exc_t else "exclusion_applies"
            detail = (f"required ONLY-IF condition(s) of {ids(only_if_failed)} are not met"
                      if reason == "only_if_condition_failed" else f"exclusion rule(s) {ids(exc_t)} are satisfied")
            return "DENY", reason, f"Not covered: {detail}, and no applicable coverage provision is satisfied.", False
        if cov_t:
            if exc_u:
                return "HUMAN_REVIEW", "exclusion_undetermined", (
                    f"Coverage rule(s) {ids(cov_t)} are satisfied, but exclusion(s) {ids(exc_u)} cannot be ruled out "
                    "with the available evidence."
                ), False
            if not self._diagnosis_supported("", extraction) and any(m.source == "diagnosis" for m in extraction.mentions):
                return "HUMAN_REVIEW", "diagnosis_not_supported", (
                    "A coverage rule is satisfied, but the claimed diagnosis is not documented in the active encounter."
                ), False
            # The diagnosis must link to the policy only when coverage rests on
            # presumptions alone; criteria met by documented evidence stand on their own.
            evidence_backed = any(
                p.status == T and not p.presumed and not p.closed_world and not p.service_only
                for e in cov_t for p in e.predicates
            )
            if not linked.diagnosis_linked_to_policy and not evidence_backed:
                return "HUMAN_REVIEW", "diagnosis_not_linked", (
                    "A coverage rule is satisfied, but the claimed diagnosis could not be linked to any indication in "
                    "the policy text."
                ), False
            return "APPROVE", "coverage_satisfied", (
                f"Coverage rule(s) {ids(cov_t)} are satisfied by the clinical evidence and no exclusion applies."
            ), False
        if disc_t:
            return "HUMAN_REVIEW", "contractor_discretion", (
                f"Rule(s) {ids(disc_t)} leave coverage to contractor (A/B MAC) discretion."
            ), False
        if cov_u or exc_u:
            return "HUMAN_REVIEW", "missing_evidence", (
                f"Applicable rule(s) {ids(cov_u + exc_u)} depend on evidence that is not documented."
            ), False
        if not evaluations:
            return "HUMAN_REVIEW", "no_applicable_rule", "No policy rule could be linked to the requested service.", False
        return "HUMAN_REVIEW", "no_rule_satisfied", (
            "Policy rules for this service were found, but none is satisfied by the documented evidence."
        ), False
