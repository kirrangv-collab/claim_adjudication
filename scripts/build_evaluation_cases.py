"""Build a research evaluation set (InferenceCase + ground_truth pairs) from the
real, publicly published CMS NCD policy sections extracted by
scripts/extract_ncd_sections.py.

IMPORTANT — what this data is and is not:
  * policy_text values are REAL, unmodified excerpts of official CMS coverage
    policy (see data/processed/ncd_eye_policy_sections.json for provenance).
  * clinical_evidence values are SYNTHETIC vignettes written for this research
    prototype. Real patient claim evidence is private health information and
    is intentionally NOT used anywhere in this project.
  * ground_truth labels are authored by the project team from a careful,
    literal reading of the cited policy text. They are a research construct
    for exercising the prototype, NOT a certified coverage determination,
    NOT clinically validated, and NOT reviewed by a claims adjudication
    professional. Do not treat this file as a benchmark of real-world
    accuracy.

Run from the project root:
    python scripts/build_evaluation_cases.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECTIONS_PATH = ROOT / "data" / "processed" / "ncd_eye_policy_sections.json"
OUT_PATH = ROOT / "data" / "processed" / "ncd_eye_evaluation_cases.jsonl"


def load_section(sections: list[dict], ncd_id: str) -> str:
    for section in sections:
        if section["ncd_section"] == ncd_id:
            return section["policy_text"]
    raise KeyError(f"NCD section {ncd_id} not found in extracted corpus")


def build_cases(sections: list[dict]) -> list[dict]:
    cases = [
        {
            "case_id": "ncd-80.7-cosmetic-keratotomy",
            "diagnosis": "myopia",
            "requested_service": "radial keratotomy",
            "policy_text": load_section(sections, "80.7"),
            "clinical_evidence": (
                "Synthetic vignette: patient requests radial keratotomy solely "
                "to correct myopia for convenience; no corneal disease, ulcer, "
                "or scarring is documented."
            ),
            "evidence_state": "documented",
            "ground_truth": "DENY",
            "rationale_note": (
                "Section 80.7 explicitly states refractive keratotomy for "
                "myopia/hyperopia correction is not covered."
            ),
        },
        {
            "case_id": "ncd-80.1-corneal-bandage",
            "diagnosis": "corneal ulcer",
            "requested_service": "hydrophilic contact lens corneal bandage",
            "policy_text": load_section(sections, "80.1"),
            "clinical_evidence": (
                "Synthetic vignette: treating ophthalmologist documents an "
                "acute corneal ulcer and applies an FDA-approved hydrophilic "
                "contact lens as a therapeutic corneal bandage."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.1 covers hydrophilic lenses used as corneal "
                "bandages for documented corneal pathology such as ulcers."
            ),
        },
        {
            "case_id": "ncd-80.8-endothelial-photography-criteria-met",
            "diagnosis": "corneal edema",
            "requested_service": "endothelial cell photography",
            "policy_text": load_section(sections, "80.8"),
            "clinical_evidence": (
                "Synthetic vignette: slit lamp examination documents corneal "
                "edema; the patient is scheduled for a secondary intraocular "
                "lens implantation."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.8 covers endothelial cell photography when the "
                "patient meets one of several listed criteria; slit-lamp "
                "corneal edema and planned secondary IOL implantation both "
                "match listed criteria."
            ),
        },
        {
            "case_id": "ncd-80.8-endothelial-photography-criteria-unclear",
            "diagnosis": "unspecified eye complaint",
            "requested_service": "endothelial cell photography",
            "policy_text": load_section(sections, "80.8"),
            "clinical_evidence": (
                "Synthetic vignette: request submitted with no slit-lamp "
                "findings, surgical plan, or contact-lens fitting documented."
            ),
            "evidence_state": "incomplete",
            "ground_truth": "HUMAN_REVIEW",
            "rationale_note": (
                "Section 80.8 coverage is conditional on specific clinical "
                "criteria; none are documented here, so this is not a clear "
                "approval or denial."
            ),
        },
        {
            "case_id": "ncd-80.9-perimetry-glaucoma",
            "diagnosis": "glaucoma",
            "requested_service": "computer enhanced perimetry",
            "policy_text": load_section(sections, "80.9"),
            "clinical_evidence": (
                "Synthetic vignette: visual field assessment ordered to "
                "monitor documented glaucoma."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.9 explicitly covers computer enhanced perimetry "
                "for assessing visual fields in glaucoma patients."
            ),
        },
        {
            "case_id": "ncd-80.2-opt-occult-evidence-gap-adversarial",
            "diagnosis": "age-related macular degeneration, occult CNV, no classic component",
            "requested_service": "ocular photodynamic therapy with verteporfin",
            "policy_text": load_section(sections, "80.2"),
            "clinical_evidence": (
                "Synthetic vignette: fluorescein angiogram confirms an occult "
                "subfoveal choroidal neovascular lesion with no classic "
                "component. Lesion size and evidence of recent progression "
                "are not documented in the submitted request."
            ),
            "evidence_state": "incomplete",
            "ground_truth": "HUMAN_REVIEW",
            "rationale_note": (
                "ADVERSARIAL/STRESS-TEST CASE, CORRECTED AFTER MODEL REVIEW. "
                "Section 80.2 states OPT is 'non-covered' for occult-only AMD "
                "lesions, but section 80.2.1(B) creates a later, narrower "
                "'Nationally Covered Indication' for exactly this diagnosis "
                "when the lesion is small (<=4 disk areas) AND shows "
                "documented progression. An LLM-assisted review pass (a "
                "feature that has since been removed from this project) "
                "correctly surfaced this superseding clause and flagged the "
                "missing size/progression "
                "evidence during testing; the case was originally mislabeled "
                "DENY before that finding. This is retained specifically to "
                "document how a naive lexical rule can not only misread a "
                "single clause (false approval risk) but also that a "
                "superficial 'correct-sounding' human-authored label can "
                "itself be wrong without checking the complete, amended "
                "policy text — motivating human review supported by full-text "
                "evidence reconciliation rather than a single keyword match."
            ),
        },
        {
            "case_id": "ncd-80.2-opt-occult-large-lesion-no-progression",
            "diagnosis": "age-related macular degeneration, occult CNV, no classic component",
            "requested_service": "ocular photodynamic therapy with verteporfin",
            "policy_text": load_section(sections, "80.2"),
            "clinical_evidence": (
                "Synthetic vignette: fluorescein angiogram confirms an occult "
                "subfoveal choroidal neovascular lesion with no classic "
                "component. Documented lesion size is 6 disk areas, and no "
                "visual acuity decline, lesion growth, or new hemorrhage is "
                "documented within the prior 3 months."
            ),
            "evidence_state": "documented",
            "ground_truth": "DENY",
            "rationale_note": (
                "Section 80.2.1(B)'s narrow covered exception for occult-only "
                "lesions requires BOTH a small lesion (<=4 disk areas) and "
                "documented progression; this vignette explicitly documents "
                "neither, so the general non-coverage rule for occult-only "
                "AMD lesions in section 80.2 applies without ambiguity."
            ),
        },
        {
            "case_id": "ncd-80.11-vitrectomy-listed-condition",
            "diagnosis": "retinal detachment secondary to vitreous strands",
            "requested_service": "vitrectomy",
            "policy_text": load_section(sections, "80.11"),
            "clinical_evidence": (
                "Synthetic vignette: retinal detachment secondary to vitreous "
                "strands is documented by the treating retinal surgeon."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.11 lists retinal detachment secondary to "
                "vitreous strands as a condition for which vitrectomy is "
                "reasonable and necessary."
            ),
        },
        {
            "case_id": "ncd-80.11-vitrectomy-unlisted-condition",
            "diagnosis": "cosmetic concern about floaters, no retinal pathology",
            "requested_service": "vitrectomy",
            "policy_text": load_section(sections, "80.11"),
            "clinical_evidence": (
                "Synthetic vignette: patient reports floaters with no "
                "documented retinal detachment, hemorrhage, or proliferative "
                "disease."
            ),
            "evidence_state": "incomplete",
            "ground_truth": "HUMAN_REVIEW",
            "rationale_note": (
                "Section 80.11 lists specific reasonable-and-necessary "
                "conditions; the request does not match any of them and "
                "there is no explicit exclusion, so conservative escalation "
                "is appropriate rather than an automatic denial."
            ),
        },
        {
            "case_id": "ncd-80.12-iol-post-cataract",
            "diagnosis": "cataract",
            "requested_service": "intraocular lens implantation",
            "policy_text": load_section(sections, "80.12"),
            "clinical_evidence": (
                "Synthetic vignette: cataract surgery with intraocular lens "
                "implantation is documented as reasonable and necessary by "
                "the operating surgeon."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.12 covers IOL implantation services and the lens "
                "itself when reasonable and necessary."
            ),
        },
    ]
    return cases


def main() -> None:
    payload = json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))
    sections = payload["sections"]
    cases = build_cases(sections)

    lines = []
    for case in cases:
        record = {
            "inference_case": {
                "case_id": case["case_id"],
                "diagnosis": case["diagnosis"],
                "requested_service": case["requested_service"],
                "policy_text": case["policy_text"],
                "clinical_evidence": case["clinical_evidence"],
                "evidence_state": case["evidence_state"],
            },
            "ground_truth": case["ground_truth"],
        }
        lines.append(json.dumps(record))

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} evaluation cases to {OUT_PATH}")


if __name__ == "__main__":
    main()
