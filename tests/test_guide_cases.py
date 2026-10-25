"""Regression test for the testing-guide case suite (data/test_cases/guide_test_cases.jsonl).

Every case must either match its expected outcome (PASS) or be a safe
abstention: the draft decision was right but confidence was below the STP
threshold, so the case went to the human queue. A wrong automated decision
(FAIL) or a missed decision with a wrong draft (CONSERVATIVE) breaks the build.
See scripts/run_test_cases.py and testing_guide.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_test_cases import load_cases, run

CASES = load_cases()


@pytest.fixture(scope="module")
def results():
    return {row["id"]: row for row in run(CASES)}


@pytest.mark.parametrize("case_id", [c["id"] for c in CASES])
def test_guide_case(results, case_id):
    row = results[case_id]
    assert row["outcome"] in {"PASS", "SAFE-ABSTAIN"}, (
        f"{case_id} {row['title']}: expected {row['expected']}, got final {row['final']} "
        f"(draft {row['draft']}, {row['reason']}): {row['rationale']}"
    )


def test_every_straight_through_decision_is_correct(results):
    automated = [r for r in results.values() if r["route"] == "STRAIGHT_THROUGH"]
    assert automated
    assert all(r["final"] == r["expected"] for r in automated)


def test_suite_covers_every_category_and_outcome():
    assert {c["id"][0] for c in CASES} == set("ABCDEFG")
    assert {c["expected_decision"] for c in CASES} == {"APPROVE", "DENY", "HUMAN_REVIEW"}
    assert len({c["id"] for c in CASES}) == len(CASES)
