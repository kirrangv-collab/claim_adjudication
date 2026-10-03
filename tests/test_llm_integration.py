"""Integration tests for the LLM/RAG engine's presence in the API surface.

These monkeypatch the required CAF_MODEL_* environment variables and stub
out backend.llm_engine.run_llm_rag_adjudication so the suite stays hermetic
(no real network calls). They verify:
* /api/v1/config discloses the engine only when actually configured, and
  never leaks the API key.
* /api/v1/adjudications attaches a secondary llm_result without changing
  the primary deterministic decision.
* The review-workflow endpoint persists the secondary signal alongside the
  deterministic suggestion for audit purposes.

The real, opt-in, network-calling check lives in
tests/test_live_llm_engine.py.
"""
import json

import pytest

from backend import llm_engine, main, reviews
from backend.schemas import LLMEngineResult


def _case(**overrides):
    case = {
        "case_id": "demo-1",
        "diagnosis": "glaucoma",
        "requested_service": "glaucoma imaging",
        "policy_text": "Glaucoma imaging is covered when medically necessary.",
        "clinical_evidence": "Documented glaucoma imaging is medically necessary.",
        "evidence_state": "documented",
    }
    case.update(overrides)
    return case


@pytest.fixture
def configured_llm_env(monkeypatch):
    monkeypatch.setenv("CAF_MODEL_PROVIDER", "gemini")
    monkeypatch.setenv(
        "CAF_MODEL_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    )
    monkeypatch.setenv("CAF_MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setenv("CAF_MODEL_ANALYSIS", "gemini-2.5-flash")


def _fake_llm_result(**overrides) -> LLMEngineResult:
    data = {
        "engine": "llm_rag",
        "model": "gemini-2.5-flash",
        "decision": "APPROVE",
        "rationale": "Coverage and evidence align in the submitted text.",
        "confidence": 0.8,
        "supporting_quotes": [],
        "attempts": 1,
        "latency_ms": 12.3,
        "error": None,
    }
    data.update(overrides)
    return LLMEngineResult(**data)


@pytest.fixture
def not_configured_llm_env(monkeypatch):
    for var in ("CAF_MODEL_PROVIDER", "CAF_MODEL_BASE_URL", "CAF_MODEL_API_KEY", "CAF_MODEL_ANALYSIS"):
        monkeypatch.delenv(var, raising=False)


def test_config_discloses_llm_only_when_configured(client, not_configured_llm_env):
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_enabled"] is False
    assert body["llm_provider"] is None
    assert "CAF_MODEL_API_KEY" not in response.text


def test_config_discloses_llm_details_without_leaking_the_key(client, configured_llm_env):
    response = client.get("/api/v1/config")
    body = response.json()
    assert body["llm_enabled"] is True
    assert body["llm_provider"] == "gemini"
    assert body["llm_analysis_model"] == "gemini-2.5-flash"
    assert body["llm_endpoint_host"] == "generativelanguage.googleapis.com"
    assert "test-key-not-real" not in response.text


def test_config_reports_gemini_defaults_with_only_an_api_key(client, monkeypatch):
    """A single key is enough to enable the engine; the rest default to Gemini."""
    for var in ("CAF_MODEL_PROVIDER", "CAF_MODEL_BASE_URL", "CAF_MODEL_ANALYSIS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CAF_MODEL_API_KEY", "test-key-not-real")

    body = client.get("/api/v1/config").json()

    assert body["llm_enabled"] is True
    assert body["llm_provider"] == "gemini"
    assert body["llm_analysis_model"] == llm_engine.DEFAULT_MODEL
    assert body["llm_endpoint_host"] == "generativelanguage.googleapis.com"
    assert "test-key-not-real" not in json.dumps(body)


async def _fake_run(case):
    return _fake_llm_result()


def test_adjudication_attaches_secondary_llm_signal_without_changing_primary_decision(
    client, configured_llm_env, monkeypatch
):
    monkeypatch.setattr(main, "run_llm_rag_adjudication", _fake_run)

    response = client.post("/api/v1/adjudications", json=_case())
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVE"  # deterministic engine, unaffected
    assert body["llm_result"]["engine"] == "llm_rag"
    assert body["llm_result"]["decision"] == "APPROVE"


def test_adjudication_omits_llm_result_when_not_configured(client, not_configured_llm_env):
    response = client.post("/api/v1/adjudications", json=_case())
    assert response.status_code == 200
    assert response.json()["llm_result"] is None


def test_adjudication_discloses_llm_failure_without_fabricating_a_decision(
    client, configured_llm_env, monkeypatch
):
    async def _failing_run(case):
        return _fake_llm_result(decision=None, rationale="", confidence=None, error="LLM engine failed after 2 attempt(s): boom")

    monkeypatch.setattr(main, "run_llm_rag_adjudication", _failing_run)

    response = client.post("/api/v1/adjudications", json=_case())
    body = response.json()
    assert body["decision"] == "APPROVE"  # deterministic engine still returns its own decision
    assert body["llm_result"]["decision"] is None
    assert "failed" in body["llm_result"]["error"]


def test_review_creation_persists_secondary_llm_signal(
    client, auth_header, configured_llm_env, monkeypatch
):
    monkeypatch.setattr(reviews, "run_llm_rag_adjudication", _fake_run)
    headers = auth_header(username="llm_maker")

    response = client.post("/api/v1/reviews", json=_case(), headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["suggested_decision"] == "APPROVE"
    assert body["llm_decision"] == "APPROVE"
    assert body["llm_result"]["rationale"]


def test_review_creation_omits_llm_result_when_not_configured(
    client, auth_header, not_configured_llm_env
):
    headers = auth_header(username="rules_only_maker")
    response = client.post("/api/v1/reviews", json=_case(), headers=headers)
    body = response.json()
    assert body["llm_decision"] is None
    assert body["llm_result"] is None
