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
    # Expansion set (v2). Ground truth for these cases was written from a
    # literal reading of the cited section BEFORE the multi-agent pipeline
    # was run on them, so they act as a semi-held-out check of the pipeline
    # (the original 10 cases above were used while developing it).
    cases += [
        {
            "case_id": "ncd-80.2-opt-occult-small-progressing",
            "diagnosis": "neovascular age-related macular degeneration, occult subfoveal CNV with no classic component",
            "requested_service": "ocular photodynamic therapy with verteporfin",
            "policy_text": load_section(sections, "80.2"),
            "clinical_evidence": (
                "Synthetic vignette: fluorescein angiogram confirms an occult "
                "subfoveal choroidal neovascular lesion with no classic "
                "component. Lesion size is 3 disk areas at initial treatment, "
                "and lesion growth was documented within the 3 months prior "
                "to initial treatment."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.2.1(B) covers occult/no-classic subfoveal CNV with "
                "verteporfin when the lesion is small (<=4 disk areas) AND has "
                "documented progression (lesion growth qualifies); both are "
                "documented. The older general non-coverage statement for "
                "occult lesions is superseded by this narrower indication."
            ),
        },
        {
            "case_id": "ncd-80.3-verteporfin-dry-amd",
            "diagnosis": "atrophic (dry) age-related macular degeneration",
            "requested_service": "verteporfin with ocular photodynamic therapy",
            "policy_text": load_section(sections, "80.3"),
            "clinical_evidence": (
                "Synthetic vignette: fundus examination documents geographic "
                "atrophy consistent with dry age-related macular degeneration; "
                "no choroidal neovascularization is present on fluorescein "
                "angiogram."
            ),
            "evidence_state": "documented",
            "ground_truth": "DENY",
            "rationale_note": (
                "Section 80.3 covers verteporfin for AMD only with neovascular "
                "AMD and qualifying CNV lesions, and lists atrophic or dry AMD "
                "as nationally non-covered."
            ),
        },
        {
            "case_id": "ncd-80.4-contact-lens-refractive-error",
            "diagnosis": "myopia",
            "requested_service": "hydrophilic contact lenses",
            "policy_text": load_section(sections, "80.4"),
            "clinical_evidence": (
                "Synthetic vignette: healthy eyes with no ocular disease; the "
                "patient requests soft hydrophilic contact lenses to correct "
                "myopia."
            ),
            "evidence_state": "documented",
            "ground_truth": "DENY",
            "rationale_note": (
                "Section 80.4: hydrophilic contact lenses are not covered when "
                "used for non-diseased eyes with spherical ametropia (myopia)."
            ),
        },
        {
            "case_id": "ncd-80.4-contact-lens-aphakia",
            "diagnosis": "aphakia",
            "requested_service": "hydrophilic contact lens",
            "policy_text": load_section(sections, "80.4"),
            "clinical_evidence": (
                "Synthetic vignette: the patient is aphakic following cataract "
                "extraction; a hydrophilic contact lens is prescribed for the "
                "aphakic eye."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.4: payment may be made under the prosthetic device "
                "benefit for hydrophilic contact lenses prescribed for an "
                "aphakic patient."
            ),
        },
        {
            "case_id": "ncd-80.5-scleral-shell-shrunken-eye",
            "diagnosis": "atrophy of globe (phthisis bulbi)",
            "requested_service": "scleral shell",
            "policy_text": load_section(sections, "80.5"),
            "clinical_evidence": (
                "Synthetic vignette: the right eye has been rendered sightless "
                "and shrunken by inflammatory disease; a scleral shell is "
                "fitted to serve as an artificial eye and avoid enucleation."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.5: where an eye has been rendered sightless and "
                "shrunken by inflammatory disease, payment may be made for a "
                "scleral shell."
            ),
        },
        {
            "case_id": "ncd-80.6-photography-diabetic-retinopathy",
            "diagnosis": "diabetic retinopathy",
            "requested_service": "intraocular photography",
            "policy_text": load_section(sections, "80.6"),
            "clinical_evidence": (
                "Synthetic vignette: the treating ophthalmologist documents "
                "diabetic retinopathy and orders fundus photography to record "
                "the retinal findings."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.6 covers intraocular photography for the diagnosis "
                "of conditions such as diabetic retinopathy."
            ),
        },
        {
            "case_id": "ncd-80.10-phaco-cataract",
            "diagnosis": "age-related cataract",
            "requested_service": "phacoemulsification cataract extraction",
            "policy_text": load_section(sections, "80.10"),
            "clinical_evidence": (
                "Synthetic vignette: a visually significant age-related "
                "cataract is documented and phacoemulsification cataract "
                "extraction is planned."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.10: reimbursement may be made for necessary "
                "services in connection with cataract extraction utilizing "
                "phaco-emulsification."
            ),
        },
        {
            "case_id": "ncd-80.7.1-ptk-corneal-scar",
            "diagnosis": "corneal scar",
            "requested_service": "phototherapeutic keratectomy",
            "policy_text": load_section(sections, "80.7"),
            "clinical_evidence": (
                "Synthetic vignette: slit lamp examination documents a central "
                "corneal scar in the visual axis; phototherapeutic keratectomy "
                "is planned to remove the scar tissue. There is no refractive "
                "indication."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "Section 80.7.1: keratoplasty that treats specific lesions of "
                "the cornea, such as phototherapeutic keratectomy removing scar "
                "tissue, is not cosmetic and may be covered."
            ),
        },
        {
            "case_id": "ncd-80.9-perimetry-family-history-only",
            "diagnosis": "family history of glaucoma",
            "requested_service": "computer enhanced perimetry",
            "policy_text": load_section(sections, "80.9"),
            "clinical_evidence": (
                "Chief complaint: routine eye examination. Exam: intraocular "
                "pressure normal; optic nerves healthy; no visual field defect "
                "noted. Family history: mother has primary open-angle glaucoma. "
                "Assessment: no glaucoma or other neuropathologic defect "
                "identified."
            ),
            "evidence_state": "documented",
            "ground_truth": "HUMAN_REVIEW",
            "rationale_note": (
                "NOISY-NOTE CASE. Section 80.9 covers perimetry for patients "
                "with glaucoma or other neuropathologic defects; glaucoma "
                "appears only in the family history and the active assessment "
                "finds none. There is no explicit exclusion, so escalation "
                "rather than automatic denial is appropriate."
            ),
        },
        {
            "case_id": "ncd-80.9-perimetry-copy-forward-note",
            "diagnosis": "primary open-angle glaucoma",
            "requested_service": "computer enhanced perimetry",
            "policy_text": load_section(sections, "80.9"),
            "clinical_evidence": (
                "Prior encounter (2024-02-11): routine refraction only; no "
                "glaucoma. Encounter 2026-05-02: HPI: progressive visual field "
                "loss. Assessment: primary open-angle glaucoma confirmed; "
                "perimetry ordered to monitor the visual fields. Electronically "
                "signed by the treating ophthalmologist."
            ),
            "evidence_state": "documented",
            "ground_truth": "APPROVE",
            "rationale_note": (
                "NOISY-NOTE CASE. The 2024 'no glaucoma' statement belongs to "
                "an earlier encounter; the active 2026 encounter documents "
                "primary open-angle glaucoma, which section 80.9 covers."
            ),
        },
        {
            "case_id": "ncd-80.11-vitrectomy-conflicting-notes",
            "diagnosis": "vitreous hemorrhage",
            "requested_service": "vitrectomy",
            "policy_text": load_section(sections, "80.11"),
            "clinical_evidence": (
                "Synthetic vignette: one note documents dense vitreous "
                "hemorrhage; a later note from the same day states the "
                "vitreous is clear."
            ),
            "evidence_state": "conflicting",
            "ground_truth": "HUMAN_REVIEW",
            "rationale_note": (
                "Section 80.11 covers vitreous opacities due to hemorrhage, but "
                "the evidence is explicitly conflicting and must be reconciled "
                "by a reviewer."
            ),
        },
        {
            "case_id": "ncd-80.1-bandage-lens-cosmetic",
            "diagnosis": "cosmetic preference, no corneal disease",
            "requested_service": "hydrophilic contact lens",
            "policy_text": load_section(sections, "80.1"),
            "clinical_evidence": (
                "Synthetic vignette: the patient requests a hydrophilic contact "
                "lens for cosmetic reasons; slit lamp examination shows a "
                "healthy cornea with no corneal pathology."
            ),
            "evidence_state": "documented",
            "ground_truth": "HUMAN_REVIEW",
            "rationale_note": (
                "Section 80.1 describes hydrophilic lenses used as corneal "
                "bandages for corneal pathology; this request has no such "
                "pathology, but 80.1 contains no explicit exclusion (that is "
                "in 80.4), so the conservative outcome is escalation."
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
