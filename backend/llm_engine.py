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

import asyncio
import json
import os
import re
import time

import httpx

from backend.schemas import InferenceCase, LLMEngineResult, VerifiedQuote

MAX_ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 20.0
_RETRY_BACKOFF_SECONDS = 0.5

DEFAULT_PROVIDER = "gemini"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"

# Thinking is pinned off so maxOutputTokens caps output only; see the module
# docstring. 1024 leaves headroom for verbatim policy quotes, which the
# previous 700-token cap left uncomfortably tight.
MAX_OUTPUT_TOKENS = 1024

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


class LLMNotConfigured(Exception):
    """Raised when the optional LLM engine has not been configured."""


def llm_configured() -> bool:
    """True once a Gemini API key is present.

    Only ``CAF_MODEL_API_KEY`` gates the engine; provider, base URL, and model
    all have Gemini defaults so a single key is enough to enable it.
    """
    return bool(os.getenv("CAF_MODEL_API_KEY"))


def llm_config_summary() -> dict[str, str | None]:
    """Non-secret configuration details safe to expose via /api/v1/config."""
    base_url = os.getenv("CAF_MODEL_BASE_URL") or DEFAULT_BASE_URL
    host = re.sub(r"^https?://", "", base_url).split("/", 1)[0]
    return {
        "provider": os.getenv("CAF_MODEL_PROVIDER") or DEFAULT_PROVIDER,
        "analysis_model": os.getenv("CAF_MODEL_ANALYSIS") or DEFAULT_MODEL,
        "endpoint_host": host,
    }


def _build_payload(case: InferenceCase) -> dict:
    user_content = (
        f"Diagnosis: {case.diagnosis}\n"
        f"Requested service: {case.requested_service}\n"
        f"Evidence completeness: {case.evidence_state}\n"
        f"Clinical evidence summary: {case.clinical_evidence}\n"
        f"Policy text:\n{case.policy_text}"
    )
    return {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def _extract_response_text(payload: dict) -> str:
    """Pull the model's text out of a Gemini generateContent response.

    Raises ``ValueError`` for every "usable response" failure mode so the
    caller can record a disclosed error instead of guessing a decision.
    """
    block_reason = (payload.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise ValueError(f"Gemini withheld the response (blockReason={block_reason}).")

    candidates = payload.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates.")

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason and finish_reason != "STOP":
        raise ValueError(f"Gemini stopped early (finishReason={finish_reason}).")

    # Thought parts are not expected (thinkingBudget is 0) but are skipped
    # defensively rather than fed to the JSON parser.
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(
        part["text"] for part in parts if part.get("text") and not part.get("thought")
    )
    if not text.strip():
        raise ValueError("Model returned an empty response.")
    return text


def _extract_json_object(content: str) -> dict:
    content = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence_match:
        content = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            content = brace_match.group(0)
    return json.loads(content)


def _parse_response(content: str, case: InferenceCase) -> tuple[str, float | None, str, list[VerifiedQuote]]:
    data = _extract_json_object(content)
    decision = data["decision"]
    if decision not in ("APPROVE", "DENY", "HUMAN_REVIEW"):
        raise ValueError(f"Model returned an unsupported decision label: {decision!r}")
    confidence = data.get("confidence")
    if confidence is not None:
        confidence = max(0.0, min(1.0, float(confidence)))
    rationale = str(data.get("rationale", "")).strip()
    raw_quotes = data.get("supporting_quotes") or []
    quotes = [
        VerifiedQuote(quote=str(q), verified_in_policy_text=str(q) in case.policy_text)
        for q in raw_quotes
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

    base_url = (os.environ.get("CAF_MODEL_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ["CAF_MODEL_API_KEY"]
    model = os.environ.get("CAF_MODEL_ANALYSIS") or DEFAULT_MODEL
    url = f"{base_url}/models/{model}:generateContent"
    started = time.perf_counter()
    last_error = "unknown error"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json=_build_payload(case),
                )
            response.raise_for_status()
            content = _extract_response_text(response.json())
            decision, confidence, rationale, quotes = _parse_response(content, case)
            return LLMEngineResult(
                engine="llm_rag",
                model=model,
                decision=decision,
                rationale=rationale,
                confidence=confidence,
                supporting_quotes=quotes,
                attempts=attempt,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=None,
            )
        except httpx.HTTPStatusError as exc:
            last_error = f"HTTP {exc.response.status_code} from model endpoint."
        except httpx.HTTPError as exc:
            last_error = f"Network error contacting model endpoint: {exc}"
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = f"Could not parse a usable response from the model: {exc}"

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

    return LLMEngineResult(
        engine="llm_rag",
        model=model,
        decision=None,
        rationale="",
        confidence=None,
        supporting_quotes=[],
        attempts=MAX_ATTEMPTS,
        latency_ms=(time.perf_counter() - started) * 1000,
        error=f"LLM engine failed after {MAX_ATTEMPTS} attempt(s): {last_error}",
    )
