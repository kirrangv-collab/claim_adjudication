"""Serve the curated real-policy research sample dataset to the API/UI.

Loads data/processed/ncd_eye_evaluation_cases.jsonl, which pairs real,
unmodified excerpts from the official CMS National Coverage Determinations
Manual with synthetic (non-patient) clinical vignettes and project-authored
ground_truth labels. See data/README.md for full provenance and the
documented limitations of these labels.
"""
import json
from functools import lru_cache
from pathlib import Path

from backend.schemas import EvaluationCase

DATA_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "processed"
    / "ncd_eye_evaluation_cases.jsonl"
)
SECTIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "processed"
    / "ncd_eye_policy_sections.json"
)


@lru_cache(maxsize=1)
def load_sample_cases() -> list[EvaluationCase]:
    if not DATA_PATH.exists():
        return []
    cases = []
    with DATA_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(EvaluationCase.model_validate_json(line))
    return cases


@lru_cache(maxsize=1)
def load_source_provenance() -> dict | None:
    if not SECTIONS_PATH.exists():
        return None
    payload = json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))
    return payload.get("provenance")
