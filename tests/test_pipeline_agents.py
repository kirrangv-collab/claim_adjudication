"""Unit tests for the five pipeline agents and their routing/safety rules."""
import json
from pathlib import Path

import pytest

from backend.config import Settings
from backend.knowledge import get_knowledge_base
from backend.llm import gemini_client
from backend.pipeline import confidence as confidence_module
from backend.pipeline import policy_logic
from backend.pipeline.clinical_evidence import ClinicalEvidenceAgent
from backend.pipeline.confidence import (
    ConfidenceExplainabilityAgent,
    validate_llm_summary,
)
from backend.pipeline.contracts import ASTNode
from backend.pipeline.knowledge_linker import KnowledgeLinkerAgent, predicate_nodes
from backend.pipeline.nlp import AssertionClassifier
from backend.pipeline.orchestrator import Pipeline
from backend.pipeline.policy_logic import (
    PolicyLogicAgent,
    _validate_llm_tree,
    refine_with_llm,
)
from backend.pipeline.reconciliation import _and, _not, _or
from backend.schemas import InferenceCase

SECTIONS = {
    s["ncd_section"]: s["policy_text"]
    for s in json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "processed" / "ncd_eye_policy_sections.json")
        .read_text(encoding="utf-8")
    )["sections"]
}


def ops(node: ASTNode) -> list[str]:
    return [node.op] + [op for child in node.children for op in ops(child)]


def parse(text: str):
    return PolicyLogicAgent().run(text)


def case(**overrides) -> InferenceCase:
    data = {
        "case_id": "unit",
        "diagnosis": "glaucoma",
        "requested_service": "glaucoma imaging",
        "policy_text": "Glaucoma imaging is covered when medically necessary.",
        "clinical_evidence": "Documented glaucoma imaging is medically necessary.",
        "evidence_state": "documented",
    }
    data.update(overrides)
    return InferenceCase(**data)


# --- Agent 1: Policy Logic ------------------------------------------------------

def test_unless_and_except_build_scoped_qualifier_nodes():
    unless = parse("Glaucoma imaging is covered unless the patient has glaucoma.").rules[0]
    assert unless.condition.op == "UNLESS"
    keratotomy = parse(SECTIONS["80.7"])
    except_rules = [r for r in keratotomy.rules if "EXCEPT" in ops(r.condition)]
    assert except_rules, "the (except ... cataract surgery) qualifier must stay inside its own clause"
    assert all(r.effect == "NOT_COVERED" for r in except_rules)


def test_only_if_with_enumerated_and_quantity_and_definition_expansion():
    rules = parse(SECTIONS["80.2"]).rules
    narrow = next(r for r in rules if r.condition.op == "ONLY_IF" and r.modality == "sufficient")
    qualifier = narrow.condition.children[1]
    assert qualifier.op == "AND"
    leaves = predicate_nodes(qualifier)
    quantity = next(leaf.quantity for leaf in leaves if leaf.quantity)
    assert (quantity.op, quantity.value, quantity.unit) == ("<=", 4.0, "disk area")
    # "evidence of progression" was replaced by its in-policy definition.
    assert {"lesion growth", "deterioration of visual acuity"} <= {leaf.text for leaf in leaves}


def test_necessary_residual_and_discretionary_rules_are_classified():
    rules = parse(SECTIONS["80.2"]).rules
    modalities = {(r.effect, r.modality) for r in rules}
    assert ("COVERED", "necessary") in modalities  # "OPT is only covered when used with verteporfin"
    assert ("NOT_COVERED", "residual") in modalities  # "Other uses ... not already addressed"
    assert any(r.effect == "DISCRETIONARY" for r in rules)


def test_trailing_only_when_is_a_constraint_that_can_deny():
    policy = ("Lumbar spinal fusion surgery is covered only when conservative therapy for at least 6 months "
              "has failed and imaging confirms spinal instability.")
    rule = parse(policy).rules[0]
    assert (rule.modality, rule.condition.op) == ("necessary", "ONLY_IF")
    trace = Pipeline().run(case(
        diagnosis="lumbar spinal instability", requested_service="lumbar spinal fusion surgery", policy_text=policy,
        clinical_evidence="Conservative therapy for 2 months has failed. Imaging confirms spinal instability.",
    ))
    assert trace.reconciliation.decision == "DENY"
    assert trace.reconciliation.reason_code == "only_if_condition_failed"


def test_and_or_not_and_lists():
    rules = parse("Hydrophilic contact lenses are not covered when used for myopia, hyperopia, or astigmatism.").rules
    assert rules[0].condition.op == "OR" and len(rules[0].condition.children) == 3
    occult = next(r for r in parse(SECTIONS["80.2"]).rules if "occult and no classic CNV lesions" in r.source_text)
    assert "NOT" in ops(occult.condition)
    fda = parse(SECTIONS["80.1"]).rules[0]
    assert fda.condition.op == "AND" and fda.subject == "hydrophilic contact lens"


def test_banner_anaphora_and_clause_splitting():
    keratoplasty = parse(SECTIONS["80.7"])
    banner = keratoplasty.rules[0]
    assert banner.effect == "NOT_COVERED" and banner.subject == "Refractive Keratoplasty"
    such = next(r for r in keratoplasty.rules if r.source_text.startswith("In addition"))
    assert such.subject_is_anaphoric and "keratotomy" in such.subject
    perimetry = parse(SECTIONS["80.9"]).rules[0]
    assert perimetry.subject == "Computer Enhanced Perimetry"
    mixed = parse("Glaucoma imaging is covered but is not covered for glaucoma.").rules
    assert [r.effect for r in mixed] == ["COVERED", "NOT_COVERED"]
    assert mixed[1].subject == "Glaucoma imaging"


def test_llm_tree_validation_rejects_hallucinated_predicates():
    clause = "OPT is covered for patients with occult lesions unless the lesion is large."
    good = {"root": "a", "nodes": [
        {"id": "a", "op": "UNLESS", "children": ["b", "c"]},
        {"id": "b", "op": "PRED", "text": "occult lesions"},
        {"id": "c", "op": "PRED", "text": "the lesion is large"},
    ]}
    assert _validate_llm_tree(good, clause, "R1").op == "UNLESS"
    bad = {"root": "a", "nodes": [{"id": "a", "op": "PRED", "text": "patient is over 65"}]}
    with pytest.raises(ValueError):
        _validate_llm_tree(bad, clause, "R1")
    cyclic = {"root": "a", "nodes": [{"id": "a", "op": "NOT", "children": ["a"]}]}
    with pytest.raises(ValueError):
        _validate_llm_tree(cyclic, clause, "R1")


@pytest.mark.asyncio
async def test_refine_with_llm_replaces_only_low_confidence_rules_and_falls_back(monkeypatch):
    structured = parse(SECTIONS["80.2"])
    low = structured.rules[0].model_copy(update={"parse_confidence": 0.4})
    structured = structured.model_copy(update={"rules": [low, *structured.rules[1:]]})

    async def fake_generate(system, user, schema, validate, **kwargs):
        return validate({"root": "a", "nodes": [{"id": "a", "op": "PRED", "text": "verteporfin"}]}), 1

    monkeypatch.setattr(gemini_client, "generate_json", fake_generate)
    refined = await refine_with_llm(structured)
    assert refined.llm_assisted_rules == 1
    assert refined.rules[0].parse_method == "llm_assisted"
    assert all(r.parse_method == "grammar" for r in refined.rules[1:])

    async def failing(*args, **kwargs):
        raise gemini_client.GeminiCallError("down", 2)

    monkeypatch.setattr(gemini_client, "generate_json", failing)
    fallback = await refine_with_llm(structured)
    assert fallback.rules[0].parse_method == "grammar"
    assert any("grammar parse kept" in note for note in fallback.notes)


def test_policy_logic_agent_has_no_shared_state_between_runs():
    agent = policy_logic.PolicyLogicAgent()
    first = agent.run(SECTIONS["80.2"])
    second = agent.run(SECTIONS["80.9"])
    assert len(second.rules) == 1 and len(first.rules) > 5


# --- Agent 2: Clinical Evidence ---------------------------------------------------------

def test_assertion_classifier_statuses():
    text = ("Occult lesion with no classic component; lesion size is not documented. "
            "Family history of glaucoma. Rule out retinal detachment.")
    classifier = AssertionClassifier(text)

    def status(phrase):
        start = text.index(phrase)
        return classifier.classify(start, start + len(phrase))

    assert status("Occult") == "present"
    assert status("classic component") == "negated"
    assert status("lesion size") == "undocumented"
    assert status("glaucoma") == "family"
    assert status("retinal detachment") == "hypothetical"


def test_negation_scope_stops_at_association_and_prefix_negation_is_local():
    text = "Subfoveal occult with no classic CNV associated with AMD"
    classifier = AssertionClassifier(text)
    assert classifier.classify(text.index("AMD"), len(text)) == "present"
    text = "non-diseased eyes with spherical ametropia"
    classifier = AssertionClassifier(text)
    start = text.index("spherical")
    assert classifier.classify(start, len(text)) == "present"


def test_active_encounter_isolation_and_icd_normalization():
    agent = ClinicalEvidenceAgent(get_knowledge_base())
    evidence = (
        "Prior encounter (2024-02-11): no glaucoma. Encounter 2026-05-02: HPI: progressive visual field loss. "
        "Assessment: primary open-angle glaucoma confirmed. Family history: mother has cataract. "
        "Electronically signed by the treating physician. Assessment: primary open-angle glaucoma confirmed."
    )
    extraction = agent.run("primary open-angle glaucoma", "perimetry", evidence)
    excluded = {s.segment_type for s in extraction.excluded_segments}
    assert {"historical", "family", "boilerplate", "duplicate"} <= excluded
    active = [m for m in extraction.mentions if m.source == "evidence" and m.assertion == "present"]
    assert "ICD10CM:H40.11" in {m.concept_id for m in active}
    historical = [m for m in extraction.mentions if m.assertion == "historical"]
    assert any(m.concept_id == "ICD10CM:H40" for m in historical)
    assert "H40.11" in extraction.icd_codes and "H40" not in extraction.icd_codes


def test_quantities_and_embedding_normalisation_of_spelling_variants():
    agent = ClinicalEvidenceAgent(get_knowledge_base())
    extraction = agent.run("macular degeneration", "OPT",
                           "Lesion measures 3 disk areas. Predominantly clasic CNV lesion.")
    assert [(q.value, q.unit) for q in extraction.quantities] == [(3.0, "disk area")]
    matches = {m.concept_id: m.match for m in extraction.mentions}
    assert matches.get("FIND:CNV_CLASSIC") == "embedding"


# --- Agent 3: Knowledge linker -------------------------------------------------------------

def test_kamel_valid_ancestors_exclude_generic_roots():
    kb = get_knowledge_base()
    ancestors = kb.valid_ancestors("ICD10CM:H40.11")
    assert "ICD10CM:H40" in ancestors
    assert ancestors["ICD10CM:H40"] == ["ICD10CM:H40.11", "ICD10CM:H40.1", "ICD10CM:H40"]
    assert "ICD10CM:H40-H42" not in ancestors and "ROOT:ICD10CM-H00-H59" not in ancestors


def test_linker_paths_relevance_and_general_negation():
    kb = get_knowledge_base()
    linker = KnowledgeLinkerAgent(kb)
    score, basis = linker.relevance("Refractive Keratoplasty", "radial keratotomy", {"PROC:RADIAL_KERATOTOMY"})
    assert score == 1.0 and "is-a" in basis
    agent = ClinicalEvidenceAgent(kb)
    extraction = agent.run("corneal scar", "PTK", "No corneal disease is present.")
    negated = [m for m in extraction.mentions if m.assertion == "negated"]
    paths = linker.link_concept("ICD10CM:H17", negated)
    assert paths and paths[0].relation == "general_negation"


# --- Agent 4: Reconciliation ------------------------------------------------------------

def test_kleene_logic():
    assert _and(["TRUE", "UNKNOWN"]) == "UNKNOWN" and _and(["FALSE", "UNKNOWN"]) == "FALSE"
    assert _or(["FALSE", "UNKNOWN"]) == "UNKNOWN" and _or(["TRUE", "UNKNOWN"]) == "TRUE"
    assert _not("UNKNOWN") == "UNKNOWN"


def test_closed_world_only_applies_to_complete_evidence():
    pipeline = Pipeline()
    policy = "Intraocular photography is covered when used for the diagnosis of glaucoma or diabetic retinopathy."
    documented = pipeline.run(case(requested_service="intraocular photography", policy_text=policy,
                                   diagnosis="cataract", clinical_evidence="Cataract is documented."))
    assert documented.reconciliation.rule_evaluations[0].status == "FALSE"
    incomplete = pipeline.run(case(requested_service="intraocular photography", policy_text=policy,
                                   diagnosis="cataract", clinical_evidence="Cataract is documented.",
                                   evidence_state="incomplete"))
    assert incomplete.reconciliation.rule_evaluations[0].status == "UNKNOWN"
    assert incomplete.reconciliation.decision == "HUMAN_REVIEW"


def test_only_if_failure_denies_and_quantities_are_compared():
    trace = Pipeline().run(case(
        diagnosis="age-related macular degeneration, occult CNV, no classic component",
        requested_service="ocular photodynamic therapy with verteporfin",
        policy_text=SECTIONS["80.2"],
        clinical_evidence=("Occult subfoveal choroidal neovascular lesion with no classic component; lesion size "
                           "is 6 disk areas and no lesion growth, visual acuity decline or hemorrhage is documented."),
    ))
    draft = trace.reconciliation
    assert draft.decision == "DENY"
    quantity = [p for e in draft.rule_evaluations for p in e.predicates if "disk areas" in p.text]
    assert quantity and quantity[0].status == "FALSE" and "violates" in quantity[0].basis


def test_request_evidence_contradiction_is_detected():
    trace = Pipeline().run(case(clinical_evidence="No evidence of glaucoma imaging is documented."))
    kinds = {c.kind for c in trace.reconciliation.contradictions}
    assert "request_evidence" in kinds
    assert trace.reconciliation.decision == "HUMAN_REVIEW"


# --- Agent 5: Confidence & routing -----------------------------------------------------------

def _draft(decision="APPROVE", **metric_overrides):
    trace = Pipeline().run(case())
    draft = trace.reconciliation
    metrics = draft.metrics.model_copy(update=metric_overrides)
    return trace, draft.model_copy(update={"decision": decision, "metrics": metrics})


def _route(draft, trace, **kwargs):
    agent = ConfidenceExplainabilityAgent(kwargs.get("stp", True), kwargs.get("approve", 0.90), kwargs.get("deny", 0.95))
    return agent.run("c", "dx", "svc", draft.metrics.evidence_state, trace.policy_logic,
                     trace.clinical_evidence, trace.knowledge_links, draft)


def test_thresholds_are_decision_specific():
    trace, draft = _draft("APPROVE")
    approved = _route(draft, trace, approve=0.90)
    assert approved.route == "STRAIGHT_THROUGH" and approved.decision == "APPROVE"
    raised = _route(draft, trace, approve=0.999)
    assert raised.route == "HUMAN_REVIEW_QUEUE" and raised.decision == "HUMAN_REVIEW"
    assert "below the APPROVE STP threshold" in raised.abstention_reasons[0]
    denied = _route(draft.model_copy(update={"decision": "DENY"}), trace, deny=0.999)
    assert denied.route == "HUMAN_REVIEW_QUEUE"


def test_missing_evidence_lowers_confidence_and_stp_can_be_disabled():
    trace, clean = _draft("APPROVE")
    _, missing = _draft("APPROVE", missing_evidence=2)
    assert _route(missing, trace).confidence < _route(clean, trace).confidence
    disabled = _route(clean, trace, stp=False)
    assert disabled.route == "HUMAN_REVIEW_QUEUE"
    assert "disabled" in disabled.abstention_reasons[-1]


def test_calibration_file_is_disclosed_and_applied(monkeypatch):
    trace, draft = _draft("APPROVE")
    baseline = _route(draft, trace)
    assert baseline.calibration["method"] in {"identity", "platt"}
    monkeypatch.setattr(confidence_module, "load_calibration", lambda: {"method": "platt", "a": 0.5, "b": -1.0, "n": 5})
    shrunk = _route(draft, trace)
    assert shrunk.confidence < baseline.confidence
    assert shrunk.calibration["method"] == "platt"


def test_audit_brief_and_llm_summary_validation():
    trace = Pipeline().run(case())
    final, extraction = trace.confidence, trace.clinical_evidence
    for heading in ("## Outcome", "## Applicable policy rules", "## Clinical evidence", "## Knowledge links",
                    "## Reconciliation", "## Confidence factors", "## Limitations"):
        assert heading in final.audit_brief
    good = ("The claim for glaucoma imaging was approved through straight-through processing. The coverage rule "
            "for glaucoma imaging is satisfied because glaucoma (H40) is documented and no exclusion applies.")
    assert validate_llm_summary(good, final, extraction)
    with pytest.raises(ValueError):
        validate_llm_summary(good.replace("approved", "denied").replace("approve", "deny"), final, extraction)
    with pytest.raises(ValueError):
        validate_llm_summary(good.replace("H40", "Z99.9"), final, extraction)


@pytest.mark.asyncio
async def test_async_pipeline_uses_validated_llm_brief_or_falls_back(monkeypatch):
    monkeypatch.setenv("CAF_MODEL_API_KEY", "test-key-not-real")
    summary = ("The pipeline approved glaucoma imaging through straight-through processing because the policy's "
               "coverage rule is satisfied by documented glaucoma and no exclusion applies to this request.")

    async def fake_generate(system, user, schema, validate, **kwargs):
        return validate({"summary": summary}), 1

    monkeypatch.setattr(gemini_client, "generate_json", fake_generate)
    trace = await Pipeline().run_async(case(), llm_assist=True)
    assert trace.confidence.audit_brief_source == "llm_assisted"
    assert summary in trace.confidence.audit_brief

    monkeypatch.undo()
    monkeypatch.setenv("CAF_MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setattr(gemini_client, "RETRY_BACKOFF_SECONDS", 0)
    hallucinated = json.dumps({"summary": summary.replace("approved", "denied")})

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": hallucinated}]}, "finishReason": "STOP"}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(gemini_client.httpx, "AsyncClient", lambda *a, **k: _Client())
    fallback = await Pipeline().run_async(case(), llm_assist=True)
    assert fallback.confidence.audit_brief_source == "template"
    assert fallback.confidence.decision == "APPROVE"


def test_settings_defaults_match_architecture_thresholds():
    settings = Settings()
    assert (settings.stp_approve_threshold, settings.stp_deny_threshold) == (0.90, 0.95)


# --- Behaviours added after the expanded guide suite ---------------------------------------

def test_or_less_quantity_is_not_split_into_an_or_list():
    rule = parse("Photodynamic therapy is covered only when the lesion is 4 disk areas or less in size.").rules[0]
    leaves = predicate_nodes(rule.condition)
    assert len(leaves) == 1
    assert (leaves[0].quantity.op, leaves[0].quantity.value, leaves[0].quantity.unit) == ("<=", 4.0, "disk area")
    # Without a number, "or more" is an ordinary OR list.
    plain = parse("Lens care is covered for mild cases or more severe cases.").rules[0]
    assert len(predicate_nodes(plain.condition)) == 2


def test_misspelled_word_gets_a_strict_spelling_match_only():
    kb = get_knowledge_base()
    mentions, _ = kb.normalizer.find("Advanced glaucomma confirmed by the ophthalmologist.")
    fuzzy = [m for m in mentions if m.match == "fuzzy"]
    assert [(m.concept_id, m.text) for m in fuzzy] == [("ICD10CM:H40", "glaucomma")]
    # Ordinary words are never fuzzy-matched to concepts.
    plain, _ = kb.normalizer.find("The treating physician requested an examination.")
    assert not [m for m in plain if m.match == "fuzzy"]


def test_effect_due_to_a_documented_cause_is_implied():
    trace = Pipeline().run(case(
        diagnosis="vitreous hemorrhage", requested_service="vitrectomy",
        policy_text="Vitrectomy is covered for vitreous opacities due to vitreous hemorrhage.",
        clinical_evidence="Dense vitreous hemorrhage documented on ultrasound.",
    ))
    predicate = trace.reconciliation.rule_evaluations[0].predicates[0]
    assert predicate.status == "TRUE"
    assert "implied by its documented cause" in predicate.basis
    assert trace.reconciliation.decision == "APPROVE"


def test_historical_finding_is_never_assumed_absent():
    policy = "Scleral shells are covered unless the eye has been enucleated."
    trace = Pipeline().run(case(
        diagnosis="dry eye syndrome", requested_service="scleral shell", policy_text=policy,
        clinical_evidence="Dry eye syndrome documented; the eye was previously removed by surgical enucleation.",
    ))
    assert trace.reconciliation.decision == "HUMAN_REVIEW"
    assert any("historical or possible" in p.basis
               for e in trace.reconciliation.rule_evaluations for p in e.predicates)


def test_requested_service_alone_is_not_clinical_evidence():
    # The service matches the policy's procedure, but nothing clinical supports it.
    trace = Pipeline().run(case(
        diagnosis="blurred vision", requested_service="phacoemulsification", policy_text=SECTIONS["80.10"],
        clinical_evidence="Blurred vision reported; lens is clear on examination.",
    ))
    assert trace.reconciliation.decision == "HUMAN_REVIEW"
    assert any(p.service_only for e in trace.reconciliation.rule_evaluations for p in e.predicates)
