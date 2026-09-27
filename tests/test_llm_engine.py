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
    """Mimics an httpx response carrying a Gemini generateContent body."""

    def __init__(
        self,
        content: str,
        status_code: int = 200,
        *,
        finish_reason: str = "STOP",
        parts: list[dict] | None = None,
    ):
        self._content = content
        self.status_code = status_code
        self._payload = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": parts
                        if parts is not None
                        else [{"text": content}],
                    },
                    "finishReason": finish_reason,
                }
            ]
        }

    def raise_for_status(self):
        if self.status_code >= 400:
            request = object()
            raise llm_engine.httpx.HTTPStatusError(
                "error", request=request, response=self
            )

    def json(self):
        return self._payload


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
    monkeypatch.setenv("CAF_MODEL_PROVIDER", "gemini")
    monkeypatch.setenv(
        "CAF_MODEL_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    )
    monkeypatch.setenv("CAF_MODEL_API_KEY", "test-key-not-real")
    monkeypatch.setenv("CAF_MODEL_ANALYSIS", "gemini-2.5-flash")
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


async def test_request_uses_gemini_url_key_header_and_disables_thinking(monkeypatch):
    """Pin the wire contract: x-goog-api-key, :generateContent path, thinking off."""
    captured: dict = {}
    good = (
        '{"decision": "APPROVE", "confidence": 0.9, "rationale": "ok", '
        '"supporting_quotes": []}'
    )

    class _RecordingClient(_FakeAsyncClient):
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers", {})
            captured["json"] = kwargs.get("json", {})
            return await super().post(url, **kwargs)

    monkeypatch.setattr(
        llm_engine.httpx, "AsyncClient", lambda *a, **kw: _RecordingClient([_FakeResponse(good)])
    )

    await llm_engine.run_llm_rag_adjudication(_case())

    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-2.5-flash:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "test-key-not-real"
    assert "Authorization" not in captured["headers"]

    config = captured["json"]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["properties"]["decision"]["enum"] == [
        "APPROVE",
        "DENY",
        "HUMAN_REVIEW",
    ]
    # The prompt is a systemInstruction, not a "role" in contents.
    assert "systemInstruction" in captured["json"]
    assert captured["json"]["contents"][0]["role"] == "user"


async def test_safety_block_is_disclosed_not_treated_as_a_decision(monkeypatch):
    blocked = _FakeResponse("")
    blocked._payload = {"promptFeedback": {"blockReason": "SAFETY"}}
    _queue_responses(monkeypatch, blocked, blocked)

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision is None
    assert "blockReason=SAFETY" in result.error


async def test_max_tokens_finish_reason_is_disclosed(monkeypatch):
    truncated = _FakeResponse("", finish_reason="MAX_TOKENS")
    _queue_responses(monkeypatch, truncated, truncated)

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision is None
    assert "finishReason=MAX_TOKENS" in result.error


async def test_missing_candidates_is_disclosed(monkeypatch):
    empty = _FakeResponse("")
    empty._payload = {}
    _queue_responses(monkeypatch, empty, empty)

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision is None
    assert "no candidates" in result.error


async def test_thought_parts_are_not_parsed_as_the_answer(monkeypatch):
    """A thought trace must not be fed to the JSON parser as model output."""
    payload = (
        '{"decision": "DENY", "confidence": 0.4, "rationale": "Excluded.", '
        '"supporting_quotes": []}'
    )
    response = _FakeResponse(
        "",
        parts=[
            {"text": "I should consider the exclusion clause...", "thought": True},
            {"text": payload, "thought": False},
        ],
    )
    _queue_responses(monkeypatch, response)

    result = await llm_engine.run_llm_rag_adjudication(_case())

    assert result.decision == "DENY"
    assert result.error is None


async def test_defaults_allow_enabling_with_only_an_api_key(monkeypatch):
    """provider/base URL/model are optional; one key must be enough."""
    for var in ("CAF_MODEL_PROVIDER", "CAF_MODEL_BASE_URL", "CAF_MODEL_ANALYSIS"):
        monkeypatch.delenv(var, raising=False)

    summary = llm_engine.llm_config_summary()
    assert summary["provider"] == llm_engine.DEFAULT_PROVIDER
    assert summary["analysis_model"] == llm_engine.DEFAULT_MODEL
    assert summary["endpoint_host"] == "generativelanguage.googleapis.com"
