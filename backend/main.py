import logging
import os
import time
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.agents import MODEL_ID, adjudicate
from backend.auth import router as auth_router
from backend.config import get_settings
from backend.evaluation import evaluate
from backend.llm_engine import (
    LLMNotConfigured,
    llm_config_summary,
    llm_configured,
    run_llm_rag_adjudication,
)
from backend.rate_limit import RateLimitMiddleware
from backend.reviews import router as reviews_router
from backend.sample_data import load_sample_cases, load_source_provenance
from backend.schemas import (
    AdjudicationResult,
    ConfigResponse,
    EvaluationRequest,
    EvaluationResponse,
    HealthResponse,
    InferenceCase,
    SampleCasesResponse,
)

settings = get_settings()
settings.require_non_default_secret()

app = FastAPI(
    title="Claim Adjudication Research Prototype",
    version="0.2.0",
    description=(
        "Leakage-aware research demonstration with a production-grade "
        "engineering layer (auth, persistence, audited human review). "
        "Adjudication content is still not clinically or legally validated "
        "and must not be used as the sole basis for a real coverage "
        "determination."
    ),
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

MAX_REQUEST_BYTES = 4 * 1024 * 1024
logger = logging.getLogger("claimlab.request")
logger.setLevel(logging.INFO)


class _RequestTooLarge(Exception):
    pass


class RequestSafetyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        try:
            request_id = str(UUID(headers.get(b"x-request-id", b"").decode("ascii")))
        except (ValueError, UnicodeDecodeError):
            request_id = str(uuid4())

        content_length = headers.get(b"content-length")
        try:
            if content_length and int(content_length) > MAX_REQUEST_BYTES:
                await self._send_too_large(send, request_id)
                return
        except ValueError:
            await self._send_error(
                send, request_id, 400, b'{"detail":"Invalid Content-Length header."}'
            )
            return

        bytes_received = 0

        async def bounded_receive():
            nonlocal bytes_received
            message = await receive()
            if message["type"] == "http.request":
                bytes_received += len(message.get("body", b""))
                if bytes_received > MAX_REQUEST_BYTES:
                    raise _RequestTooLarge()
            return message

        started = time.perf_counter()
        status_code = 500

        async def secure_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                ])
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, bounded_receive, secure_send)
        except _RequestTooLarge:
            await self._send_too_large(send, request_id)
            return

        logger.info(
            "request_complete id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            scope["method"],
            scope["path"],
            status_code,
            (time.perf_counter() - started) * 1000,
        )

    @staticmethod
    async def _send_too_large(send, request_id):
        await RequestSafetyMiddleware._send_error(
            send,
            request_id,
            413,
            b'{"detail":"Request body exceeds the 4 MiB limit."}',
        )

    @staticmethod
    async def _send_error(send, request_id, status, body):
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"x-request-id", request_id.encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


app.add_middleware(RequestSafetyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CLAIMLAB_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(auth_router)
app.include_router(reviews_router)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", service="claim-adjudication-api", prototype=True,
        benchmark_available=False,
    )


@app.get("/api/v1/config", response_model=ConfigResponse)
def config() -> ConfigResponse:
    llm_enabled = llm_configured()
    llm_summary = llm_config_summary() if llm_enabled else {}
    return ConfigResponse(
        model=MODEL_ID,
        inference_type="deterministic_rules",
        llm_enabled=llm_enabled,
        llm_provider=llm_summary.get("provider"),
        llm_analysis_model=llm_summary.get("analysis_model"),
        llm_endpoint_host=llm_summary.get("endpoint_host"),
        llm_role=(
            "Secondary comparison signal only; never overrides the "
            "deterministic decision and is not itself authoritative."
            if llm_enabled else None
        ),
        environment=settings.environment,
        auth_required=True,
        audit_trail_enabled=True,
        production_ready=False,
        clinically_validated=False,
        available_models=[MODEL_ID],
        unavailable_baselines={
            "B1": "Original implementation is not present in this workspace.",
            "B2": "Original implementation is not present in this workspace.",
            "B3": "Original implementation is not present; an independent LLM/RAG comparison engine is available instead when configured.",
        },
        supported_decisions=["APPROVE", "DENY", "HUMAN_REVIEW"],
        benchmark_available=False,
    )


@app.post("/api/v1/adjudications", response_model=AdjudicationResult)
async def create_adjudication(case: InferenceCase) -> AdjudicationResult:
    result = adjudicate(case)
    if llm_configured():
        try:
            llm_result = await run_llm_rag_adjudication(case)
        except LLMNotConfigured:
            llm_result = None
        result = result.model_copy(update={"llm_result": llm_result})
    return result


@app.post("/api/v1/evaluations", response_model=EvaluationResponse)
def create_evaluation(request: EvaluationRequest) -> EvaluationResponse:
    return evaluate(request)


@app.get("/api/v1/sample-cases", response_model=SampleCasesResponse)
def get_sample_cases() -> SampleCasesResponse:
    return SampleCasesResponse(
        cases=load_sample_cases(),
        provenance=load_source_provenance(),
        is_real_world_policy_text=True,
        is_synthetic_clinical_evidence=True,
        ground_truth_caveat=(
            "ground_truth labels in this sample were authored by the project "
            "team from a literal reading of the real CMS policy text. They "
            "are a research construct for exercising the prototype, not a "
            "certified coverage determination or a clinically validated "
            "benchmark. See data/README.md for full methodology."
        ),
    )
