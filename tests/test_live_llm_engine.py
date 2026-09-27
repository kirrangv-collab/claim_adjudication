"""Opt-in, real-network test for the LLM/RAG engine (Google Gemini).

Skipped automatically unless CAF_MODEL_API_KEY is set, so CI never depends
on network access or credentials. Everything else defaults to Gemini
(see backend/llm_engine.py), so a single key is enough:

    $env:CAF_MODEL_API_KEY = "..."
    python -m pytest tests/test_live_llm_engine.py -v

To target a different 2.5-series model, also set:

    $env:CAF_MODEL_ANALYSIS = "gemini-2.5-flash"

This makes exactly one real network call with synthetic, non-sensitive
text, and asserts only on the *shape* of a real response (a real decision
label was produced) rather than a specific decision, since a live model's
exact output can vary between calls/versions.
"""
import os

import pytest

from backend.llm_engine import DEFAULT_MODEL, llm_configured, run_llm_rag_adjudication
from backend.schemas import InferenceCase

pytestmark = pytest.mark.skipif(
    not llm_configured(),
    reason="Set CAF_MODEL_API_KEY to run this live Gemini test.",
)


async def test_live_engine_returns_a_real_decision_for_a_clear_cut_case():
    case = InferenceCase(
        case_id="live-smoke-test",
        diagnosis="glaucoma",
        requested_service="glaucoma imaging",
        policy_text="Glaucoma imaging is covered when medically necessary.",
        clinical_evidence="Documented glaucoma imaging is medically necessary.",
        evidence_state="documented",
    )

    result = await run_llm_rag_adjudication(case)

    assert result.error is None, f"Live call failed: {result.error}"
    assert result.decision in ("APPROVE", "DENY", "HUMAN_REVIEW")
    assert result.model == os.environ.get("CAF_MODEL_ANALYSIS", DEFAULT_MODEL)
    assert result.latency_ms > 0
    assert result.rationale
