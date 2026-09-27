"""Unit tests for the optional LLM/RAG comparison engine.

These tests never make a real network call: httpx.AsyncClient is replaced
with an in-memory fake that returns pre-scripted responses, so the suite
stays fast and hermetic. The opt-in, real-network test lives in
tests/test_live_llm_engine.py and is skipped unless a real endpoint is
configured via environment variables.
"""
import pytest

from backend import llm_engine
from backend.schemas import InferenceCase

pytestmark = pytest.mark.asyncio


def _case(**overrides) -> InferenceCase:
    data = {
        "diagnosis": "glaucoma",
        "requested_service": "glaucoma imaging",
        "policy_text": "Glaucoma imaging is covered when medically necessary.",
        "clinical_evidence": "Documented glaucoma imaging is medically necessary.",
        "evidence_state": "documented",
    }
    data.update(overrides)
    return InferenceCase(**data)


class _FakeResponse:
    def __init__(self, content: str, status_code: int = 200):
        self._content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = object()
            raise llm_engine.httpx.HTTPStatusError(
                "error", request=request, response=self
            )

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeAsyncClient:
    def __init__(self, queue: list):
        self._queue = queue

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, *args, **kwargs):
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch):
    monkeypatch.setenv("CAF_MODEL_PROVIDER", "vllm")
    monkeypatch.setenv("CAF_MODEL_BASE_URL", "https://example-test-host/v1")
    monkeypatch.setenv("CAF_MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setenv("CAF_MODEL_ANALYSIS", "test-model")
    monkeypatch.setattr(llm_engine, "_RETRY_BACKOFF_SECONDS", 0)


def _queue_responses(monkeypatch, *responses):
    queue = list(responses)
    monkeypatch.setattr(
        llm_engine.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(queue)
    )


async def test_engine_not_configured_raises(monkeypatch):
    monkeypatch.delenv("CAF_MODEL_API_KEY", raising=False)
    with pytest.raises(llm_engine.LLMNotConfigured):
        await llm_engine.run_llm_rag_adjudication(_case())


async def test_successful_response_is_parsed_and_quotes_verified(monkeypatch):
    content = (
        '{"decision": "APPROVE", "confidence": 0.82, '
        '"rationale": "Coverage and evidence align.", '
        '"supporting_quotes": ["Glaucoma imaging is covered when medically necessary.", '
        '"a quote that does not appear in the policy text"]}'
    )
    _queue_responses(monkeypatch, _FakeResponse(content))

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision == "APPROVE"
    assert result.confidence == 0.82
    assert result.attempts == 1
    assert result.error is None
    assert result.supporting_quotes[0].verified_in_policy_text is True
    assert result.supporting_quotes[1].verified_in_policy_text is False


async def test_markdown_fenced_json_is_parsed(monkeypatch):
    content = (
        "```json\n"
        '{"decision": "DENY", "confidence": 0.6, "rationale": "Excluded.", '
        '"supporting_quotes": []}\n'
        "```"
    )
    _queue_responses(monkeypatch, _FakeResponse(content))

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision == "DENY"
    assert result.error is None


async def test_malformed_response_retries_then_succeeds(monkeypatch):
    good_content = (
        '{"decision": "HUMAN_REVIEW", "confidence": 0.5, '
        '"rationale": "Ambiguous.", "supporting_quotes": []}'
    )
    _queue_responses(monkeypatch, _FakeResponse("not json at all"), _FakeResponse(good_content))

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision == "HUMAN_REVIEW"
    assert result.attempts == 2
    assert result.error is None


async def test_all_attempts_failing_discloses_error_without_a_decision(monkeypatch):
    _queue_responses(
        monkeypatch,
        _FakeResponse("still not json"),
        _FakeResponse("still not json"),
    )

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision is None
    assert result.attempts == llm_engine.MAX_ATTEMPTS
    assert result.error is not None
    assert "failed after" in result.error


async def test_empty_content_is_treated_as_a_failure(monkeypatch):
    _queue_responses(monkeypatch, _FakeResponse(""), _FakeResponse(""))

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision is None
    assert result.error is not None


async def test_unsupported_decision_label_is_rejected(monkeypatch):
    bad_content = '{"decision": "MAYBE", "rationale": "x", "supporting_quotes": []}'
    _queue_responses(monkeypatch, _FakeResponse(bad_content), _FakeResponse(bad_content))

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision is None
    assert result.error is not None
