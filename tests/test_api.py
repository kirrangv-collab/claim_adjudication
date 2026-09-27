import pytest
from fastapi.testclient import TestClient

from backend.main import RequestSafetyMiddleware, app

client = TestClient(app)


def valid_case(**overrides):
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


def test_health_and_config_disclose_missing_benchmark(monkeypatch):
    for var in ("CAF_MODEL_PROVIDER", "CAF_MODEL_BASE_URL", "CAF_MODEL_API_KEY", "CAF_MODEL_ANALYSIS"):
        monkeypatch.delenv(var, raising=False)
    assert client.get("/api/v1/health").json()["benchmark_available"] is False
    config = client.get("/api/v1/config").json()
    assert set(config["unavailable_baselines"]) == {"B1", "B2", "B3"}
    assert config["llm_enabled"] is False
    assert config["production_ready"] is False


def test_adjudication_uses_only_inference_case_and_returns_agent_trace():
    response = client.post("/api/v1/adjudications", json=valid_case())
    assert response.status_code == 200
    result = response.json()
    assert result["decision"] == "APPROVE"
    assert [finding["agent"] for finding in result["findings"]] == [
        "policy_interpreter", "evidence_assessor", "reconciler"
    ]
    assert result["case_id"] == "demo-1"


def test_conflict_escalates_to_human_review():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(evidence_state="conflicting"),
    )
    assert response.json()["decision"] == "HUMAN_REVIEW"


def test_explicit_matching_exclusion_denies():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(policy_text="Glaucoma imaging is not covered."),
    )
    assert response.json()["decision"] == "DENY"


def test_policy_exception_is_escalated_not_overridden_by_keyword_match():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(
            policy_text="Glaucoma imaging is covered unless the patient has glaucoma."
        ),
    )
    assert response.json()["decision"] == "HUMAN_REVIEW"


def test_conflicting_policy_statements_are_escalated():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(
            policy_text="Glaucoma imaging is covered but is not covered for glaucoma."
        ),
    )
    assert response.json()["decision"] == "HUMAN_REVIEW"


def test_negated_clinical_evidence_is_not_treated_as_support():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(
            clinical_evidence="No evidence of glaucoma imaging is documented."
        ),
    )
    assert response.json()["decision"] == "HUMAN_REVIEW"


def test_inference_rejects_ground_truth_field():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(ground_truth="DENY"),
    )
    assert response.status_code == 422


def test_evaluation_keeps_failed_case_in_total_denominator():
    cases = [
        {
            "inference_case": valid_case(),
            "ground_truth": "APPROVE",
        },
        {
            "inference_case": valid_case(case_id="bad"),
            "ground_truth": "DENY",
        },
    ]
    from backend.evaluation import evaluate
    from backend.schemas import EvaluationRequest

    count = 0

    def flaky_predictor(case):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("simulated model failure")
        from backend.agents import adjudicate
        return adjudicate(case)

    result = evaluate(EvaluationRequest(cases=cases), predictor=flaky_predictor)
    assert result.total_cases == 2
    assert result.evaluated_cases == 1
    assert result.errored_cases == 1
    assert result.accuracy == 0.5
    assert result.per_class["DENY"].support == 1
    assert result.per_class["DENY"].recall == 0
    assert result.false_denial_rate == 0
    assert "RuntimeError" in result.errors[0]
    assert "simulated model failure" not in result.errors[0]
    assert result.case_results[1].status == "error"


def test_api_evaluation_separates_labels_from_inference_payload():
    response = client.post(
        "/api/v1/evaluations",
        json={
            "cases": [
                {
                    "inference_case": valid_case(),
                    "ground_truth": "APPROVE",
                }
            ]
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["total_cases"] == 1
    assert result["evaluated_cases"] == 1
    assert result["per_class"]["APPROVE"]["support"] == 1
    assert result["confusion_matrix"]["APPROVE"]["APPROVE"] == 1


def test_security_headers_and_valid_request_id_are_returned():
    import uuid

    request_id = str(uuid.uuid4())
    response = client.get(
        "/api/v1/health", headers={"X-Request-ID": request_id}
    )
    assert response.headers["x-request-id"] == request_id
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_oversized_content_length_is_rejected():
    response = client.post(
        "/api/v1/adjudications",
        headers={"Content-Length": str(5 * 1024 * 1024)},
        content=b"",
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_streamed_request_body_is_limited_without_content_length():
    body = b"x" * (4 * 1024 * 1024 + 1)
    events = iter([
        {"type": "http.request", "body": body[:4 * 1024 * 1024], "more_body": True},
        {"type": "http.request", "body": body[4 * 1024 * 1024:], "more_body": False},
    ])
    sent = []

    async def receive():
        return next(events)

    async def send(message):
        sent.append(message)

    async def app_that_reads_body(scope, receive, send):
        while (await receive()).get("more_body"):
            pass

    middleware = RequestSafetyMiddleware(app_that_reads_body)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/adjudications",
            "headers": [],
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 413
