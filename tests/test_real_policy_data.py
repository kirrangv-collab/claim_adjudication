"""End-to-end tests against the real, downloaded CMS policy dataset.

These tests exercise the full API using genuine excerpts from the official
CMS National Coverage Determinations Manual (see data/README.md for
provenance and data/manifest.json for the source/checksum record), paired
with clearly-synthetic clinical vignettes. No real patient data is used
anywhere in this project.

Some assertions below intentionally document KNOWN LIMITATIONS of the
deterministic lexical adjudicator discovered while building this dataset
(see scripts/build_evaluation_cases.py for the full rationale of each case).
They exist to make regressions/improvements visible, not to assert that the
prototype is accurate on real-world policy language.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
SAMPLE_JSONL = DATA_DIR / "ncd_eye_evaluation_cases.jsonl"
SECTIONS_JSON = DATA_DIR / "ncd_eye_policy_sections.json"


def _load_sample_records() -> list[dict]:
    with SAMPLE_JSONL.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_real_policy_dataset_is_present_with_provenance():
    assert SAMPLE_JSONL.exists(), (
        "Real-policy evaluation fixture is missing. Run "
        "scripts/extract_ncd_sections.py then "
        "scripts/build_evaluation_cases.py from the project root."
    )
    sections = json.loads(SECTIONS_JSON.read_text(encoding="utf-8"))
    provenance = sections["provenance"]
    assert provenance["publisher"] == "Centers for Medicare & Medicaid Services (CMS)"
    assert provenance["source_url"].startswith("https://www.cms.gov/")
    assert "source_sha256" in provenance


def test_sample_cases_endpoint_serves_real_data_with_caveats():
    response = client.get("/api/v1/sample-cases")
    assert response.status_code == 200
    body = response.json()
    assert body["is_real_world_policy_text"] is True
    assert body["is_synthetic_clinical_evidence"] is True
    assert "not a certified coverage determination" in body["ground_truth_caveat"]
    assert len(body["cases"]) == len(_load_sample_records())
    # Spot-check that served policy_text actually matches the extracted
    # source-of-truth sections file (integrity, not just presence).
    sections_by_id = {
        s["ncd_section"]: s["policy_text"]
        for s in json.loads(SECTIONS_JSON.read_text(encoding="utf-8"))["sections"]
    }
    keratotomy_case = next(
        c for c in body["cases"]
        if c["inference_case"]["case_id"] == "ncd-80.7-cosmetic-keratotomy"
    )
    assert keratotomy_case["inference_case"]["policy_text"] == sections_by_id["80.7"]


def test_full_dataset_round_trips_through_evaluation_without_errors():
    records = _load_sample_records()
    response = client.post("/api/v1/evaluations", json={"cases": records})
    assert response.status_code == 200
    result = response.json()
    assert result["total_cases"] == len(records)
    assert result["evaluated_cases"] == len(records)
    assert result["errored_cases"] == 0

    by_case_id = {
        row["case_id"]: row for row in result["case_results"]
    }

    # Cases the deterministic engine gets right on real, complex policy text.
    assert by_case_id["ncd-80.1-corneal-bandage"]["prediction"] == "APPROVE"
    assert by_case_id["ncd-80.9-perimetry-glaucoma"]["prediction"] == "APPROVE"
    assert by_case_id["ncd-80.12-iol-post-cataract"]["prediction"] == "APPROVE"
    assert (
        by_case_id["ncd-80.2-opt-occult-evidence-gap-adversarial"]["prediction"]
        == "HUMAN_REVIEW"
    )

    # KNOWN LIMITATION (documented, not asserted as acceptable): the lexical
    # engine approves this case even though the applicable narrow exception
    # (small lesion + documented progression) is explicitly NOT met, because
    # it detects a "covered" keyword elsewhere in the long policy text. This
    # is the core motivation for the evidence-reconciliation research
    # direction; a fix here should be a deliberate, tested improvement.
    known_false_approval = by_case_id["ncd-80.2-opt-occult-large-lesion-no-progression"]
    assert known_false_approval["ground_truth"] == "DENY"
    assert known_false_approval["prediction"] == "APPROVE", (
        "If this now correctly predicts DENY, the lexical engine's clause "
        "disambiguation has improved — update this test's expectation and "
        "the accompanying documentation/README claims about known limits."
    )

    # KNOWN LIMITATION: an "except ... cataract surgery" qualifier anywhere
    # in the policy text triggers conservative escalation, even when the
    # actual request has no connection to that narrow exception.
    known_over_escalation = by_case_id["ncd-80.7-cosmetic-keratotomy"]
    assert known_over_escalation["ground_truth"] == "DENY"
    assert known_over_escalation["prediction"] == "HUMAN_REVIEW"
