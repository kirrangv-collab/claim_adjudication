"""Optional second adjudication engine: LLM reasoning over the submitted policy text.

This engine is a secondary, comparison-only signal. It is invoked only when a
server operator has supplied a Google Gemini API key via the ``CAF_MODEL_*``
environment variables below; it is never required for the core deterministic
engine (see ``backend/agents.py``) to function, and its output never overrides
or feeds into the deterministic decision.

Design constraints (deliberate, and load-bearing for the safety story):

* Bounded retries only. A fixed, small number of attempts are made; after
  they are exhausted the engine returns ``decision=None`` with a disclosed
  ``error`` string. It never silently falls back to a specific decision
  label on failure — that would misrepresent an outage as a real signal.
* Quotes the model claims support its rationale are checked against the
  submitted policy text and flagged ``verified_in_policy_text=False`` if
  they do not literally occur there, so a hallucinated quote is visible to
  the reviewer rather than silently trusted.
* The prompt asks only about the text actually submitted with the case; the
  engine has no external retrieval/browsing and no access to any other
  claim, patient, or case.

Gemini-specific notes (these replace the previous vLLM-oriented behaviour and
are worth reading before changing the request payload):

* ``thinkingBudget`` is pinned to ``0``. Gemini Flash models think by
  default, and ``maxOutputTokens`` is a *combined* budget covering thought
  tokens and output tokens. Without this, a "reasoning" trace can consume
  the entire budget and the call returns ``finishReason=MAX_TOKENS`` with an
  empty response — while still billing for the thought tokens. Pinning the
  budget to 0 keeps ``maxOutputTokens`` meaningful as an output-only cap.
  Support for ``thinkingBudget`` is *per model id*, not per series: as of
  this writing ``gemini-3.8-flash`` and ``gemini-flash-latest`` accept and
  honour ``thinkingBudget: 0`` (verified: zero thought parts,
  ``finishReason=STOP``), while ``gemini-3.5-flash-lite`` rejects it with
  HTTP 400. Re-verify this before changing the pinned id rather than
  assuming a whole series behaves uniformly.
* The previous default, ``gemini-2.5-flash``, now returns HTTP 404
  ("no longer available to new users") for newly issued keys, so it is no
  longer a usable default. Gemini also retires model ids over time, so a
  pinned id can break again; ``gemini-flash-latest`` tracks the current
  Flash release but changes behaviour when Google updates it, so the pinned
  version id is kept as the default for reproducibility.
* ``responseMimeType="application/json"`` plus ``responseSchema`` make Gemini
  return schema-valid JSON directly, instead of relying on the model to
  honour "respond with ONLY a JSON object". The response is still parsed
  defensively, because an enforcement failure surfaces as an HTTP error or a
  non-JSON body rather than a typed error.
* The Gemini ``generateContent`` API has no ``seed`` parameter, so the fixed
  seed that the previous vLLM configuration sent no longer exists. Run-to-run
  variance is reduced only by ``temperature=0``; it is not eliminated. This
  engine can still return a different decision for an identical request
  between calls, which is disclosed rather than hidden, and is itself a
  reason the engine must stay a secondary, human-reviewed signal.
* Gemini applies safety filters that can withhold output entirely. A block
  (``promptFeedback.blockReason``) or a ``SAFETY``/``MAX_TOKENS`` finish is
  reported as a disclosed engine failure — never as a decision.

The HTTP transport, payload construction and response extraction live in
``backend/llm/gemini_client.py`` and are shared with the pipeline's LLM
assists.

Environment variables. Only ``CAF_MODEL_API_KEY`` is required; the rest
default to the values below, so pointing the engine at Gemini is a matter of
supplying one key:

    CAF_MODEL_API_KEY    Gemini API key; never logged or returned to clients.
                         This is the only required variable.
    CAF_MODEL_PROVIDER   defaults to "gemini" (disclosure only)
    CAF_MODEL_BASE_URL   defaults to the Gemini v1beta REST base; the model id
                         is appended as "/models/{model}:generateContent"
    CAF_MODEL_ANALYSIS   defaults to "gemini-3.8-flash"; set a Flash model id
                         that accepts ``thinkingBudget: 0`` to override
"""
from __future__ import annotations

import time

import httpx  # noqa: F401 - re-exported so tests can patch llm_engine.httpx.AsyncClient

from backend.llm import gemini_client
from backend.llm.gemini_client import GeminiCallError, GeminiNotConfigured
from backend.schemas import InferenceCase, LLMEngineResult, VerifiedQuote

# Re-exported for backwards compatibility (tests, scripts and the live check).
DEFAULT_PROVIDER = gemini_client.DEFAULT_PROVIDER
DEFAULT_BASE_URL = gemini_client.DEFAULT_BASE_URL
DEFAULT_MODEL = gemini_client.DEFAULT_MODEL
MAX_OUTPUT_TOKENS = gemini_client.MAX_OUTPUT_TOKENS
_extract_json_object = gemini_client.extract_json_object
_extract_response_text = gemini_client.extract_response_text

MAX_ATTEMPTS = gemini_client.MAX_ATTEMPTS

_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["APPROVE", "DENY", "HUMAN_REVIEW"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "supporting_quotes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "rationale", "supporting_quotes"],
    "propertyOrdering": ["decision", "confidence", "rationale", "supporting_quotes"],
}

_SYSTEM_PROMPT = (
    "You are a research-only claims policy assistant. You are given a "
    "requested service, a diagnosis, an excerpt of insurance policy text, "
    "and a summary of clinical evidence. Decide whether, based ONLY on the "
    "text provided (do not use outside medical or policy knowledge), the "
    "request should be APPROVE, DENY, or HUMAN_REVIEW (use HUMAN_REVIEW "
    "whenever the policy text is qualified, conditional, ambiguous, or the "
    "evidence is incomplete or conflicting). Quote the exact policy "
    "sentence(s) that justify your decision, copied verbatim from the "
    "policy text. Respond with ONLY a single JSON object, no other text, "
    "matching this schema: "
    '{"decision": "APPROVE|DENY|HUMAN_REVIEW", "confidence": <0..1 float>, '
    '"rationale": "<one or two sentences>", "supporting_quotes": '
    '["<verbatim quote from the policy text>", ...]}'
)


class LLMNotConfigured(GeminiNotConfigured):
    """Raised when the optional LLM engine has not been configured."""


def llm_configured() -> bool:
    """True once a Gemini API key is present (``CAF_MODEL_API_KEY``)."""
    return gemini_client.gemini_configured()


def llm_config_summary() -> dict[str, str | None]:
    """Non-secret configuration details safe to expose via /api/v1/config."""
    return gemini_client.config_summary()


def _user_content(case: InferenceCase) -> str:
    return (
        f"Diagnosis: {case.diagnosis}\n"
        f"Requested service: {case.requested_service}\n"
        f"Evidence completeness: {case.evidence_state}\n"
        f"Clinical evidence summary: {case.clinical_evidence}\n"
        f"Policy text:\n{case.policy_text}"
    )


def _build_payload(case: InferenceCase) -> dict:
    return gemini_client.build_payload(
        _SYSTEM_PROMPT, _user_content(case), _RESPONSE_SCHEMA, gemini_client.MAX_OUTPUT_TOKENS
    )


def _parse_data(data: dict, case: InferenceCase) -> tuple[str, float | None, str, list[VerifiedQuote]]:
    decision = data["decision"]
    if decision not in ("APPROVE", "DENY", "HUMAN_REVIEW"):
        raise ValueError(f"Model returned an unsupported decision label: {decision!r}")
    confidence = data.get("confidence")
    if confidence is not None:
        confidence = max(0.0, min(1.0, float(confidence)))
    rationale = str(data.get("rationale", "")).strip()
    quotes = [
        VerifiedQuote(quote=str(q), verified_in_policy_text=str(q) in case.policy_text)
        for q in data.get("supporting_quotes") or []
        if str(q).strip()
    ]
    return decision, confidence, rationale, quotes


async def run_llm_rag_adjudication(case: InferenceCase) -> LLMEngineResult:
    """Run the secondary LLM/RAG engine with bounded retries.

    Raises ``LLMNotConfigured`` if the required environment variables are
    not set; callers should treat that as "feature disabled", not an error.
    """
    if not llm_configured():
        raise LLMNotConfigured()
    model = gemini_client.gemini_model()
    started = time.perf_counter()
    try:
        (decision, confidence, rationale, quotes), attempts = await gemini_client.generate_json(
            _SYSTEM_PROMPT,
            _user_content(case),
            _RESPONSE_SCHEMA,
            lambda data: _parse_data(data, case),
            max_attempts=MAX_ATTEMPTS,
        )
    except GeminiCallError as exc:
        return LLMEngineResult(
            engine="llm_rag",
            model=model,
            decision=None,
            rationale="",
            confidence=None,
            supporting_quotes=[],
            attempts=exc.attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=f"LLM engine failed after {exc.attempts} attempt(s): {exc}",
        )
    return LLMEngineResult(
        engine="llm_rag",
        model=model,
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        supporting_quotes=quotes,
        attempts=attempts,
        latency_ms=(time.perf_counter() - started) * 1000,
        error=None,
    )
