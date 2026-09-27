"""Optional second adjudication engine: LLM reasoning over the submitted policy text.

This engine is a secondary, comparison-only signal. It is invoked only when a
server operator has configured a vLLM-compatible endpoint via the
``CAF_MODEL_*`` environment variables below; it is never required for the
core deterministic engine (see ``backend/agents.py``) to function, and its
output never overrides or feeds into the deterministic decision.

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
* ``temperature=0`` and a fixed ``seed`` are sent to reduce (not guarantee
  eliminate) run-to-run variance. Empirically, this server can still return
  a different decision for an identical request between calls: this is
  disclosed rather than hidden, and is itself a reason the engine must stay
  a secondary, human-reviewed signal.

Environment variables (unset by default; the engine is disabled until all
of ``CAF_MODEL_PROVIDER``, ``CAF_MODEL_BASE_URL``, ``CAF_MODEL_API_KEY``, and
``CAF_MODEL_ANALYSIS`` are present):

    CAF_MODEL_PROVIDER   e.g. "vllm"
    CAF_MODEL_BASE_URL   e.g. "https://your-vllm-host/v1"
    CAF_MODEL_API_KEY    bearer token; never logged or returned to clients
    CAF_MODEL_ANALYSIS   the actual model id to call (not a catalog alias)
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

_REQUIRED_ENV_VARS = (
    "CAF_MODEL_PROVIDER",
    "CAF_MODEL_BASE_URL",
    "CAF_MODEL_API_KEY",
    "CAF_MODEL_ANALYSIS",
)

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
    return all(os.getenv(var) for var in _REQUIRED_ENV_VARS)


def llm_config_summary() -> dict[str, str | None]:
    """Non-secret configuration details safe to expose via /api/v1/config."""
    base_url = os.getenv("CAF_MODEL_BASE_URL")
    host = None
    if base_url:
        host = re.sub(r"^https?://", "", base_url).split("/", 1)[0]
    return {
        "provider": os.getenv("CAF_MODEL_PROVIDER"),
        "analysis_model": os.getenv("CAF_MODEL_ANALYSIS"),
        "endpoint_host": host,
    }


def _build_messages(case: InferenceCase) -> list[dict[str, str]]:
    user_content = (
        f"Diagnosis: {case.diagnosis}\n"
        f"Requested service: {case.requested_service}\n"
        f"Evidence completeness: {case.evidence_state}\n"
        f"Clinical evidence summary: {case.clinical_evidence}\n"
        f"Policy text:\n{case.policy_text}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


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

    base_url = os.environ["CAF_MODEL_BASE_URL"].rstrip("/")
    api_key = os.environ["CAF_MODEL_API_KEY"]
    model = os.environ["CAF_MODEL_ANALYSIS"]
    started = time.perf_counter()
    last_error = "unknown error"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": _build_messages(case),
                        "temperature": 0,
                        "max_tokens": 700,
                        "seed": 7,
                        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                    },
                )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not content or not content.strip():
                raise ValueError("Model returned an empty response.")
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
