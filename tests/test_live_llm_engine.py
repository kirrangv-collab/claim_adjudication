"""Opt-in, real-network test for the LLM/RAG engine.

Skipped automatically unless CAF_MODEL_PROVIDER, CAF_MODEL_BASE_URL,
CAF_MODEL_API_KEY, and CAF_MODEL_ANALYSIS are all set in the environment,
so CI never depends on network access or credentials. Run locally with:

    $env:CAF_MODEL_PROVIDER = "vllm"
    $env:CAF_MODEL_BASE_URL = "https://your-approved-vllm-host/v1"
    $env:CAF_MODEL_API_KEY = "..."
    $env:CAF_MODEL_ANALYSIS = "your-model-id"
    python -m pytest tests/test_live_llm_engine.py -v

This makes exactly one real network call with synthetic, non-sensitive
text, and asserts only on the *shape* of a real response (a real decision
label was produced) rather than a specific decision, since a live model's
exact output can vary between calls/versions.
"""
import os

import pytest

from backend.llm_engine import llm_configured, run_llm_rag_adjudication
from backend.schemas import InferenceCase

pytestmark = pytest.mark.skipif(
    not llm_configured(),
    reason="Set CAF_MODEL_PROVIDER/BASE_URL/API_KEY/ANALYSIS to run this live test.",
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
    assert result.model == os.environ["CAF_MODEL_ANALYSIS"]
    assert result.latency_ms > 0
    assert result.rationale
