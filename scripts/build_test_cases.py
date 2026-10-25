"""Build the expanded manual/automated test-case suite used by testing_guide.md.

Writes data/test_cases/guide_test_cases.jsonl. Each line is:

    {
      "id": "A01", "category": "...", "title": "...", "checks": "...",
      "expected_decision": "APPROVE" | "DENY" | "HUMAN_REVIEW",
      "inference_case": {...}
    }

``expected_decision`` is the outcome a careful human reading of the policy
text would expect *and* that this system is designed to produce (including
its deliberately conservative behaviour, e.g. escalating rather than denying
when an UNLESS/EXCEPT clause is triggered). Expectations were written before
the suite was first run, and are research constructs — not certified
coverage determinations. Clinical evidence is synthetic; real CMS policy text
is loaded unmodified from data/processed/ncd_eye_policy_sections.json.

Run from the project root:
    python scripts/build_test_cases.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECTIONS = {
    s["ncd_section"]: s["policy_text"]
    for s in json.loads((ROOT / "data" / "processed" / "ncd_eye_policy_sections.json").read_text(encoding="utf-8"))["sections"]
}
OUT_PATH = ROOT / "data" / "test_cases" / "guide_test_cases.jsonl"


def case(id_, category, title, checks, expected, diagnosis, service, policy, evidence, state="documented"):
    return {
        "id": id_,
        "category": category,
        "title": title,
        "checks": checks,
        "expected_decision": expected,
        "inference_case": {
            "case_id": f"guide-{id_}",
            "diagnosis": diagnosis,
            "requested_service": service,
            "policy_text": policy,
            "clinical_evidence": evidence,
            "evidence_state": state,
        },
    }


A = "A · Policy logic (Agent 1)"
B = "B · Quantities (Agents 1/2/4)"
C = "C · Evidence completeness (Agents 4/5)"
D = "D · Clinical assertions & ontology (Agents 2/3)"
E = "E · Real CMS NCD policies"
F = "F · Outside the eye ontology"
G = "G · Robustness & safety"

CONTACT_EXCEPT = "Contact lenses are not covered except for patients with aphakia."
SCLERAL_UNLESS = "Scleral shells are covered unless the eye has been enucleated."
PACHY_ONLY = "Corneal pachymetry is covered only when glaucoma is documented."
ECP_AND = ("Endothelial cell photography is covered when the patient has corneal edema and is about to "
           "undergo cataract surgery.")
PDT_SIZE = "Photodynamic therapy is covered only when the lesion is 4 disk areas or less in size."
OXYGEN = "Home oxygen therapy is covered only when resting oxygen saturation is 88% or less."

CASES = [
    # --- A: policy logic -------------------------------------------------------------
    case("A01", A, "Simple coverage + specific diagnosis", "COVERED rule; H40.11 satisfies 'glaucoma' via KAMEL valid ancestor",
         "APPROVE", "primary open-angle glaucoma", "optical coherence tomography",
         "Optical coherence tomography is covered for patients with glaucoma.",
         "Primary open-angle glaucoma confirmed on exam; optical coherence tomography ordered."),
    case("A02", A, "Unconditional exclusion", "NOT_COVERED rule with TRUE condition",
         "DENY", "myopia", "eyeglasses", "Eyeglasses are not covered.",
         "Patient requests new eyeglasses to correct myopia."),
    case("A03", A, "EXCEPT clause — exception met", "Exception lifts the exclusion but nothing grants coverage → conservative escalation",
         "HUMAN_REVIEW", "aphakia", "contact lenses", CONTACT_EXCEPT,
         "Patient is aphakic after cataract extraction; contact lenses prescribed."),
    case("A04", A, "EXCEPT clause — exception not met", "EXCEPT(TRUE, aphakia) with aphakia absent from a complete record → exclusion applies",
         "DENY", "myopia", "contact lenses", CONTACT_EXCEPT,
         "Healthy eyes; contact lenses requested to correct myopia."),
    case("A05", A, "UNLESS clause — not triggered", "UNLESS(TRUE, enucleated) with no enucleation documented → coverage",
         "APPROVE", "dry eye syndrome", "scleral shell", SCLERAL_UNLESS,
         "Severe dry eye syndrome with lacrimal gland failure; scleral shell fitted to protect the cornea."),
    case("A06", A, "UNLESS clause — triggered", "Coverage condition fails; no explicit exclusion → conservative escalation",
         "HUMAN_REVIEW", "dry eye syndrome", "scleral shell", SCLERAL_UNLESS,
         "Dry eye syndrome documented; the eye was previously removed by surgical enucleation."),
    case("A07", A, "ONLY IF — condition met", "Only-if rule is the whole coverage criterion",
         "APPROVE", "glaucoma", "corneal pachymetry", PACHY_ONLY,
         "Open-angle glaucoma documented by the treating ophthalmologist; corneal pachymetry performed."),
    case("A08", A, "ONLY IF — condition not met", "Only-if condition fails → denial",
         "DENY", "routine eye examination", "corneal pachymetry", PACHY_ONLY,
         "Routine eye examination; no glaucoma; corneal pachymetry requested."),
    case("A09", A, "AND — both conditions met", "AND(corneal edema, about to undergo cataract surgery)",
         "APPROVE", "corneal edema", "endothelial cell photography", ECP_AND,
         "Slit lamp shows corneal edema; patient is about to undergo cataract surgery."),
    case("A10", A, "AND — one condition negated", "One AND branch FALSE → no rule satisfied",
         "HUMAN_REVIEW", "corneal edema", "endothelial cell photography", ECP_AND,
         "Slit lamp shows corneal edema; no cataract surgery is planned."),
    case("A11", A, "OR list via ontology", "E11.35 proliferative diabetic retinopathy is-a diabetic retinopathy",
         "APPROVE", "proliferative diabetic retinopathy", "fundus photography",
         "Fundus photography is covered for diabetic retinopathy, macular degeneration, or retinal neoplasms.",
         "Type 2 diabetes with proliferative diabetic retinopathy documented; fundus photography ordered."),
    case("A12", A, "Specific exclusion vs general coverage", "Exclusion satisfied, coverage condition absent → denial",
         "DENY", "vitreous floaters", "vitrectomy",
         "Vitrectomy is covered for vitreous hemorrhage. Vitrectomy is not covered for floaters.",
         "Patient reports floaters; no vitreous hemorrhage or retinal pathology is present."),
    # --- B: quantities ---------------------------------------------------------------
    case("B01", B, "Quantity within limit", "≤ 4 disk areas satisfied by 3",
         "APPROVE", "neovascular age-related macular degeneration", "photodynamic therapy", PDT_SIZE,
         "Neovascular age-related macular degeneration; the lesion measures 3 disk areas."),
    case("B02", B, "Quantity over limit", "≤ 4 disk areas violated by 5.5 → only-if fails",
         "DENY", "neovascular age-related macular degeneration", "photodynamic therapy", PDT_SIZE,
         "Neovascular age-related macular degeneration; the lesion measures 5.5 disk areas."),
    case("B03", B, "Quantity not documented", "Measurements are never assumed (no closed-world for quantities)",
         "HUMAN_REVIEW", "neovascular age-related macular degeneration", "photodynamic therapy", PDT_SIZE,
         "Neovascular age-related macular degeneration; lesion size is not documented."),
    case("B04", B, "Percentage threshold met", "≤ 88 % satisfied by 85 %",
         "APPROVE", "chronic hypoxemia", "home oxygen therapy", OXYGEN,
         "Resting oxygen saturation measured at 85% on room air."),
    case("B05", B, "Percentage threshold not met", "≤ 88 % violated by 93 %",
         "DENY", "chronic hypoxemia", "home oxygen therapy", OXYGEN,
         "Resting oxygen saturation measured at 93% on room air."),
    case("B06", B, "Duration threshold", "'at least 3 months' satisfied by 4 months",
         "APPROVE", "convergence insufficiency", "vision therapy",
         "Vision therapy is covered only when conservative treatment for at least 3 months has failed.",
         "Conservative treatment for 4 months has failed."),
    # --- C: evidence completeness -------------------------------------------------------
    case("C01", C, "Complete evidence → straight-through", "Baseline for C02/C03",
         "APPROVE", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Glaucoma documented; computer enhanced perimetry ordered to assess the visual fields."),
    case("C02", C, "Same evidence declared incomplete", "Draft APPROVE but routed to the human queue",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Glaucoma documented; computer enhanced perimetry ordered to assess the visual fields.", "incomplete"),
    case("C03", C, "Same evidence declared conflicting", "Contradiction → human queue",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Glaucoma documented; computer enhanced perimetry ordered to assess the visual fields.", "conflicting"),
    # --- D: assertions & ontology --------------------------------------------------------
    case("D01", D, "Negated diagnosis", "Claimed diagnosis negated by evidence → contradiction",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "No evidence of glaucoma or other neuropathologic defect on examination."),
    case("D02", D, "Family history only", "Family-history section is excluded from the active encounter",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Exam: optic nerves healthy, intraocular pressure normal. Family history: father has glaucoma."),
    case("D03", D, "Hypothetical diagnosis", "'Rule out glaucoma' is hypothetical, not a diagnosis",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Rule out glaucoma; perimetry requested."),
    case("D04", D, "Explicitly undocumented", "'not documented' → UNKNOWN, missing evidence",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Glaucoma status is not documented in the referral."),
    case("D05", D, "Past history only", "PMH section is historical, not the active encounter",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Past medical history: glaucoma. Assessment: routine refraction only, eyes otherwise healthy."),
    case("D06", D, "Specific subtype via valid ancestor", "H40.2 angle-closure glaucoma is-a H40 glaucoma",
         "APPROVE", "angle-closure glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Primary angle-closure glaucoma confirmed by gonioscopy; perimetry ordered."),
    case("D07", D, "Misspelled term", "Strict spelling-variant match of 'glaucomma' to glaucoma (H40)",
         "APPROVE", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Advanced glaucomma confirmed by the treating ophthalmologist; perimetry ordered."),
    case("D08", D, "Other neuropathologic defect", "Multiple sclerosis is-a neuropathologic defect",
         "APPROVE", "multiple sclerosis", "computer enhanced perimetry", SECTIONS["80.9"],
         "Multiple sclerosis with a visual field defect documented by the neurologist."),
    # --- E: real CMS NCD policies ----------------------------------------------------------
    case("E01", E, "80.5 scleral shell for dry eye", "'covered as a prosthetic device … treatment of dry eye'",
         "APPROVE", "dry eye syndrome", "scleral shell", SECTIONS["80.5"],
         "Severe dry eye; the lacrimal gland fails and a scleral shell is used with artificial tears."),
    case("E02", E, "80.6 photography for macular degeneration", "Listed condition",
         "APPROVE", "macular degeneration", "intraocular photography", SECTIONS["80.6"],
         "Macular degeneration documented; intraocular photography ordered by the ophthalmologist."),
    case("E03", E, "80.6 photography with no listed condition", "Closed-world: no listed condition present",
         "HUMAN_REVIEW", "routine eye examination", "intraocular photography", SECTIONS["80.6"],
         "Healthy eyes with no retinal disease; photography requested for records."),
    case("E04", E, "80.8 endothelial photography for cornea guttata", "Bulleted criteria list",
         "APPROVE", "cornea guttata", "endothelial cell photography", SECTIONS["80.8"],
         "Slit lamp evidence of endothelial dystrophy (cornea guttata) before cataract surgery."),
    case("E05", E, "80.11 vitrectomy for proliferative diabetic retinopathy", "E11.35 is-a proliferative retinopathy",
         "APPROVE", "proliferative diabetic retinopathy", "vitrectomy", SECTIONS["80.11"],
         "Type 2 diabetes with proliferative diabetic retinopathy and traction documented by the retina surgeon."),
    case("E06", E, "80.11 vitrectomy for vitreous hemorrhage", "'vitreous opacities due to vitreous hemorrhage': documented cause implies the effect",
         "APPROVE", "vitreous hemorrhage", "vitrectomy", SECTIONS["80.11"],
         "Dense non-clearing vitreous hemorrhage documented on B-scan ultrasound."),
    case("E07", E, "80.4 contact lens for keratoconus", "Neither the exclusion nor the aphakia coverage applies",
         "HUMAN_REVIEW", "keratoconus", "hydrophilic contact lens", SECTIONS["80.4"],
         "Keratoconus documented; a hydrophilic contact lens is requested."),
    case("E08", E, "80.3 verteporfin, predominantly classic CNV", "Covered indication",
         "APPROVE", "neovascular age-related macular degeneration with predominantly classic subfoveal CNV",
         "verteporfin with ocular photodynamic therapy", SECTIONS["80.3"],
         "Fluorescein angiogram confirms neovascular age-related macular degeneration with predominantly classic "
         "subfoveal choroidal neovascular lesions."),
    case("E09", E, "80.3 verteporfin for pathologic myopia", "Contractor (MAC) discretion → human review",
         "HUMAN_REVIEW", "pathologic myopia", "verteporfin with ocular photodynamic therapy", SECTIONS["80.3"],
         "Pathologic myopia with choroidal neovascularization documented."),
    case("E10", E, "80.2 juxtafoveal CNV", "Nationally non-covered indication",
         "DENY", "age-related macular degeneration with juxtafoveal CNV", "ocular photodynamic therapy with verteporfin",
         SECTIONS["80.2"], "Fluorescein angiogram shows juxtafoveal CNV lesions associated with age-related macular degeneration."),
    case("E11", E, "80.7 LASIK for myopia", "LASIK is-a keratomileusis is-a refractive keratoplasty (Not Covered banner)",
         "DENY", "myopia", "LASIK", SECTIONS["80.7"],
         "Patient requests LASIK to correct myopia so that glasses are no longer needed."),
    case("E12", E, "80.10 phaco without documented cataract", "Coverage requires cataract extraction context",
         "HUMAN_REVIEW", "blurred vision", "phacoemulsification", SECTIONS["80.10"],
         "Blurred vision reported; lens is clear on examination."),
    case("E13", E, "80.1 bandage lens for corneal erosion", "Listed corneal pathology",
         "APPROVE", "recurrent corneal erosion", "hydrophilic contact lens corneal bandage", SECTIONS["80.1"],
         "Recurrent corneal erosion documented; an FDA-approved hydrophilic contact lens is applied as a corneal bandage."),
    case("E14", E, "80.12 IOL after cataract surgery", "Covered if reasonable and necessary",
         "APPROVE", "age-related cataract", "intraocular lens implantation", SECTIONS["80.12"],
         "Age-related cataract removed by phacoemulsification; intraocular lens implantation performed."),
    # --- F: outside the eye ontology ------------------------------------------------------------
    case("F01", F, "Non-eye coverage (lexical fallback)", "No ontology concepts; lexical matching of conditions",
         "APPROVE", "obstructive sleep apnea", "home sleep testing",
         "Home sleep testing is covered when daytime sleepiness and loud snoring are documented.",
         "Patient reports daytime sleepiness, and loud snoring is documented by the spouse."),
    case("F02", F, "Non-eye cosmetic exclusion", "Unconditional exclusion of a cosmetic service",
         "DENY", "glabellar lines", "cosmetic botulinum toxin injection",
         "Cosmetic botulinum toxin injections are not covered.",
         "Patient requests botulinum toxin injection for cosmetic reasons."),
    case("F03", F, "Irrelevant policy", "No rule concerns the requested service",
         "HUMAN_REVIEW", "cataract", "cataract surgery",
         "Dental implants are covered when tooth loss is documented.",
         "Visually significant cataract documented."),
    # --- G: robustness & safety --------------------------------------------------------------------
    case("G01", G, "All-caps input", "Case-insensitive parsing and matching",
         "APPROVE", "GLAUCOMA", "GLAUCOMA IMAGING", "GLAUCOMA IMAGING IS COVERED WHEN MEDICALLY NECESSARY.",
         "DOCUMENTED GLAUCOMA; GLAUCOMA IMAGING IS MEDICALLY NECESSARY."),
    case("G02", G, "Noisy multi-section note", "Template, prior encounter, family history, signature and copy-forward are excluded",
         "APPROVE", "primary open-angle glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Template: this note was generated automatically. Prior encounter (2023-01-05): no glaucoma. "
         "Family history: mother has cataract. Encounter 2026-06-01: Assessment: primary open-angle glaucoma "
         "confirmed; perimetry ordered. Electronically signed by the ophthalmologist. "
         "Assessment: primary open-angle glaucoma confirmed; perimetry ordered."),
    case("G03", G, "Contradiction inside the active note", "Concept both affirmed and negated → contradiction",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"],
         "Glaucoma is confirmed on today's exam. The optic nerve review shows no glaucoma."),
    case("G04", G, "HTML / script / unicode input", "Rendered as text, never executed; no crash",
         "HUMAN_REVIEW", '<script>alert(1)</script> & "quoted"', "O'Brien's <b>procedure</b>",
         'Coverage is <i>covered</i> when "medically necessary"; see § 80.1 — <script>evil()</script>.',
         "Evidence includes <img src=x onerror=alert(1)> and unicode: café, 日本語, emoji 🎉."),
    case("G05", G, "Minimal evidence", "Nearly empty evidence never auto-approves",
         "HUMAN_REVIEW", "glaucoma", "computer enhanced perimetry", SECTIONS["80.9"], "ok"),
]


def main() -> None:
    ids = [c["id"] for c in CASES]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate case ids")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in CASES) + "\n", encoding="utf-8")
    print(f"Wrote {len(CASES)} test cases to {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
