"""End-to-end tests against the real, downloaded CMS policy dataset.

These tests exercise the full API using genuine excerpts from the official
CMS National Coverage Determinations Manual (see data/README.md for
provenance and data/manifest.json for the source/checksum record), paired
with clearly-synthetic clinical vignettes. No real patient data is used
anywhere in this project.

The multi-agent pipeline assertions pin current behaviour on this small,
project-labelled sample so regressions are visible; they are not a claim of
real-world accuracy. The B1 lexical baseline assertions document its KNOWN
LIMITATIONS (see scripts/build_evaluation_cases.py for each case's
rationale), which the pipeline was designed to address.
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


def _evaluate(engine: str) -> dict:
    records = _load_sample_records()
    response = client.post("/api/v1/evaluations", json={"cases": records, "engine": engine})
    assert response.status_code == 200
    result = response.json()
    assert result["total_cases"] == len(records)
    assert result["evaluated_cases"] == len(records)
    assert result["errored_cases"] == 0
    return result


def test_pipeline_round_trips_full_dataset_and_fixes_b1_limitations():
    result = _evaluate("multi_agent_pipeline")
    by_case_id = {row["case_id"]: row for row in result["case_results"]}

    # Every draft decision matches the project-authored label on this sample.
    assert result["draft_accuracy"] == 1.0
    assert all(row["draft_decision"] == row["ground_truth"] for row in result["case_results"])
    # No automated outcome contradicts a label: no false approvals/denials.
    assert result["false_approval_rate"] == 0
    assert result["false_denial_rate"] == 0
    assert result["stp_accuracy"] == 1.0

    # B1 false approval: the narrow 80.2.1(B) exception (small lesion AND
    # progression) is evaluated as a clause-scoped ONLY_IF and fails.
    large_lesion = by_case_id["ncd-80.2-opt-occult-large-lesion-no-progression"]
    assert large_lesion["prediction"] == "DENY"
    assert large_lesion["route"] == "STRAIGHT_THROUGH"

    # B1 over-escalation: "(except ... cataract surgery)" is scoped to its own
    # clause, so it no longer blocks the explicit refractive exclusion.
    assert by_case_id["ncd-80.7-cosmetic-keratotomy"]["prediction"] == "DENY"

    # Missing size/progression evidence keeps the coverage exception open.
    gap = by_case_id["ncd-80.2-opt-occult-evidence-gap-adversarial"]
    assert gap["prediction"] == "HUMAN_REVIEW"
    assert gap["route"] == "HUMAN_REVIEW_QUEUE"

    # Agent 2 noisy-note isolation: family history / earlier encounters.
    assert by_case_id["ncd-80.9-perimetry-family-history-only"]["prediction"] == "HUMAN_REVIEW"
    assert by_case_id["ncd-80.9-perimetry-copy-forward-note"]["prediction"] == "APPROVE"


def test_b1_baseline_known_limitations_remain_documented():
    result = _evaluate("b1_lexical")
    by_case_id = {row["case_id"]: row for row in result["case_results"]}

    # KNOWN LIMITATION: a "covered" keyword elsewhere in the long policy text
    # produces a false approval although the narrow exception is NOT met.
    known_false_approval = by_case_id["ncd-80.2-opt-occult-large-lesion-no-progression"]
    assert known_false_approval["ground_truth"] == "DENY"
    assert known_false_approval["prediction"] == "APPROVE"

    # KNOWN LIMITATION: any "except" qualifier anywhere triggers escalation.
    known_over_escalation = by_case_id["ncd-80.7-cosmetic-keratotomy"]
    assert known_over_escalation["ground_truth"] == "DENY"
    assert known_over_escalation["prediction"] == "HUMAN_REVIEW"

    # KNOWN LIMITATION: glaucoma appearing only in family history still matches.
    assert by_case_id["ncd-80.9-perimetry-family-history-only"]["prediction"] == "APPROVE"


def test_compare_endpoint_on_real_dataset_shows_pipeline_gain():
    records = _load_sample_records()
    body = client.post("/api/v1/evaluations/compare", json={"cases": records}).json()
    pipeline, baseline = body["engines"]["multi_agent_pipeline"], body["engines"]["b1_lexical"]
    assert pipeline["accuracy"] > baseline["accuracy"]
    assert pipeline["false_approval_rate"] < baseline["false_approval_rate"]
    assert body["disagreements"]