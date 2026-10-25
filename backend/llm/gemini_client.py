"""Shared Google Gemini ``generateContent`` JSON client.

Used by the comparison LLM/RAG engine (``backend/llm_engine.py``) and by the
pipeline's optional LLM assists (Agent 1 clause parsing, Agent 5 audit-brief
prose). See ``backend/llm_engine.py`` for the rationale behind the pinned
request settings (thinking budget 0, schema-enforced JSON, temperature 0).

Every call has bounded retries and either returns a validated value or
raises :class:`GeminiCallError` with a disclosed reason; callers must fall
back to deterministic behaviour on failure, never guess.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from typing import TypeVar

import httpx

DEFAULT_PROVIDER = "gemini"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"
REQUEST_TIMEOUT_SECONDS = 20.0
RETRY_BACKOFF_SECONDS = 0.5
MAX_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 1024

T = TypeVar("T")


class GeminiNotConfigured(Exception):
    """Raised when no Gemini API key is configured."""


class GeminiCallError(Exception):
    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


def gemini_configured() -> bool:
    return bool(os.getenv("CAF_MODEL_API_KEY"))


def gemini_model() -> str:
    return os.environ.get("CAF_MODEL_ANALYSIS") or DEFAULT_MODEL


def config_summary() -> dict[str, str | None]:
    base_url = os.getenv("CAF_MODEL_BASE_URL") or DEFAULT_BASE_URL
    host = re.sub(r"^https?://", "", base_url).split("/", 1)[0]
    return {
        "provider": os.getenv("CAF_MODEL_PROVIDER") or DEFAULT_PROVIDER,
        "analysis_model": gemini_model(),
        "endpoint_host": host,
    }


def build_payload(system_prompt: str, user_content: str, response_schema: dict, max_output_tokens: int) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def extract_response_text(payload: dict) -> str:
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
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part["text"] for part in parts if part.get("text") and not part.get("thought"))
    if not text.strip():
        raise ValueError("Model returned an empty response.")
    return text


def extract_json_object(content: str) -> dict:
    content = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence_match:
        content = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            content = brace_match.group(0)
    return json.loads(content)


async def generate_json(
    system_prompt: str,
    user_content: str,
    response_schema: dict,
    validate: Callable[[dict], T],
    *,
    max_attempts: int = MAX_ATTEMPTS,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
) -> tuple[T, int]:
    """Call Gemini and return ``(validate(json), attempts)``.

    ``validate`` runs inside the retry loop: raising ``ValueError``/``KeyError``
    there counts as an unusable response and triggers a retry.
    """
    if not gemini_configured():
        raise GeminiNotConfigured()
    base_url = (os.environ.get("CAF_MODEL_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/models/{gemini_model()}:generateContent"
    headers = {"x-goog-api-key": os.environ["CAF_MODEL_API_KEY"], "Content-Type": "application/json"}
    payload = build_payload(system_prompt, user_content, response_schema, max_output_tokens)
    last_error = "unknown error"
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return validate(extract_json_object(extract_response_text(response.json()))), attempt
        except httpx.HTTPStatusError as exc:
            last_error = f"HTTP {exc.response.status_code} from model endpoint."
        except httpx.HTTPError as exc:
            last_error = f"Network error contacting model endpoint: {exc}"
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_error = f"Could not parse a usable response from the model: {exc}"
        if attempt < max_attempts:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
    raise GeminiCallError(last_error, max_attempts)
