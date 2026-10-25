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


def test_health_and_config_disclose_pipeline_and_missing_benchmark(monkeypatch):
    for var in ("CAF_MODEL_PROVIDER", "CAF_MODEL_BASE_URL", "CAF_MODEL_API_KEY", "CAF_MODEL_ANALYSIS"):
        monkeypatch.delenv(var, raising=False)
    health = client.get("/api/v1/health").json()
    assert health["benchmark_available"] is False
    assert health["knowledge_backends"]["vector_store"] == "memory"
    config = client.get("/api/v1/config").json()
    assert config["model"] == "multi_agent_pipeline"
    assert config["inference_type"] == "multi_agent_pipeline"
    assert set(config["unavailable_baselines"]) == {"B2", "B3"}
    assert config["available_models"] == ["multi_agent_pipeline", "b1_lexical"]
    assert [a["id"] for a in config["pipeline"]["agents"]] == [
        "policy_logic", "clinical_evidence", "knowledge_linker", "reconciliation", "confidence_explainability",
    ]
    assert config["pipeline"]["stp_thresholds"] == {"APPROVE": 0.9, "DENY": 0.95}
    assert config["pipeline"]["llm_assist_enabled"] is False
    assert config["llm_enabled"] is False
    assert config["production_ready"] is False


def test_adjudication_uses_only_inference_case_and_returns_five_agent_trace():
    response = client.post("/api/v1/adjudications", json=valid_case())
    assert response.status_code == 200
    result = response.json()
    assert result["decision"] == "APPROVE"
    assert result["draft_decision"] == "APPROVE"
    assert result["route"] == "STRAIGHT_THROUGH"
    assert result["confidence"] >= 0.9
    assert [finding["agent"] for finding in result["findings"]] == [
        "policy_logic", "clinical_evidence", "knowledge_linker", "reconciliation", "confidence_explainability",
    ]
    assert result["case_id"] == "demo-1"
    trace = result["pipeline"]
    assert trace["policy_logic"]["rules"][0]["effect"] == "COVERED"
    assert "ICD10CM:H40" in {m["concept_id"] for m in trace["clinical_evidence"]["mentions"]}
    assert set(trace["agent_latency_ms"]) == {
        "policy_logic", "clinical_evidence", "knowledge_linker", "reconciliation", "confidence_explainability",
    }
    assert result["audit_brief"].startswith("# Claim adjudication audit brief")
    assert result["baseline"]["model"] == "b1_lexical"


def test_incomplete_evidence_is_routed_to_human_review_with_reasons():
    response = client.post("/api/v1/adjudications", json=valid_case(evidence_state="incomplete"))
    result = response.json()
    assert result["route"] == "HUMAN_REVIEW_QUEUE"
    assert result["decision"] == "HUMAN_REVIEW"
    assert result["pipeline"]["confidence"]["abstention_reasons"]


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


def test_lex_specialis_denial_is_never_auto_finalised():
    response = client.post(
        "/api/v1/adjudications",
        json=valid_case(policy_text="Glaucoma imaging is covered but is not covered for glaucoma."),
    )
    result = response.json()
    assert result["draft_decision"] == "DENY"
    assert result["route"] == "HUMAN_REVIEW_QUEUE"
    assert any("overrides a satisfied coverage" in r for r in result["pipeline"]["confidence"]["abstention_reasons"])


def test_evaluation_engine_selection_and_stp_metrics():
    body = {"cases": [{"inference_case": valid_case(), "ground_truth": "APPROVE"}]}
    pipeline = client.post("/api/v1/evaluations", json=body).json()
    assert pipeline["model"] == "multi_agent_pipeline"
    assert pipeline["stp_rate"] == 1.0
    assert pipeline["stp_accuracy"] == 1.0
    assert pipeline["draft_accuracy"] == 1.0
    assert pipeline["case_results"][0]["route"] == "STRAIGHT_THROUGH"

    baseline = client.post("/api/v1/evaluations", json={**body, "engine": "b1_lexical"}).json()
    assert baseline["model"] == "b1_lexical"
    assert baseline["stp_rate"] is None


def test_llm_engine_evaluation_requires_configuration(monkeypatch):
    monkeypatch.delenv("CAF_MODEL_API_KEY", raising=False)
    body = {"cases": [{"inference_case": valid_case(), "ground_truth": "APPROVE"}], "engine": "llm_rag"}
    assert client.post("/api/v1/evaluations", json=body).status_code == 400


def test_compare_endpoint_reports_each_engine_and_disagreements(monkeypatch):
    monkeypatch.delenv("CAF_MODEL_API_KEY", raising=False)
    cases = [
        {"inference_case": valid_case(case_id="a"), "ground_truth": "APPROVE"},
        {
            "inference_case": valid_case(
                case_id="b", policy_text="Glaucoma imaging is covered except when the patient has glaucoma."
            ),
            "ground_truth": "HUMAN_REVIEW",
        },
    ]
    response = client.post("/api/v1/evaluations/compare", json={"cases": cases, "include_llm": True})
    assert response.status_code == 200
    body = response.json()
    assert set(body["engines"]) == {"multi_agent_pipeline", "b1_lexical"}
    assert all(e["total_cases"] == 2 for e in body["engines"].values())
    assert isinstance(body["disagreements"], list)
