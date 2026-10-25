"""Tests for authentication, STP routing and the human-review workflow.

These exercise straight-through processing of high-confidence cases, the
maker-checker segregation of duties for human-routed cases, decision
immutability, and admin-only reopen behavior that make the review workflow
suitable as a genuine audit trail rather than just a data store.
"""


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


def human_case(**overrides):
    """A case Agent 5 routes to the human queue (evidence not declared complete)."""
    return valid_case(evidence_state="incomplete", **overrides)


def test_protected_endpoints_require_authentication(client):
    response = client.post("/api/v1/reviews", json=valid_case())
    assert response.status_code == 401
    response = client.get("/api/v1/reviews")
    assert response.status_code == 401


def test_login_rejects_bad_credentials_and_accepts_good_ones(client, make_user):
    username, password = make_user("carol")
    bad = client.post(
        "/api/v1/auth/login", data={"username": username, "password": "wrong"}
    )
    assert bad.status_code == 401

    good = client.post(
        "/api/v1/auth/login", data={"username": username, "password": password}
    )
    assert good.status_code == 200
    body = good.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "reviewer"

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["username"] == username


def test_high_confidence_case_is_finalised_by_straight_through_processing(client, auth_header):
    headers = auth_header(username="maker1")
    response = client.post("/api/v1/reviews", json=valid_case(), headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["suggested_decision"] == "APPROVE"
    assert body["route"] == "STRAIGHT_THROUGH"
    assert body["status"] == "auto_decided"
    assert body["final_decision"] == "APPROVE"
    assert body["decision_source"] == "stp"
    assert body["human_decision"] is None
    assert body["created_by"] == "maker1"
    assert body["audit_brief"].startswith("# Claim adjudication audit brief")
    assert body["pipeline"]["confidence"]["route"] == "STRAIGHT_THROUGH"


def test_low_confidence_case_goes_to_human_queue_with_draft_suggestion(client, auth_header):
    headers = auth_header(username="maker1b")
    body = client.post("/api/v1/reviews", json=human_case(), headers=headers).json()
    assert body["status"] == "pending_review"
    assert body["route"] == "HUMAN_REVIEW_QUEUE"
    assert body["suggested_decision"] == "APPROVE"
    assert body["final_decision"] is None
    assert body["decision_source"] is None


def test_auto_decided_case_is_immutable_until_admin_reopens(client, auth_header):
    maker = auth_header(username="maker_stp")
    checker = auth_header(username="checker_stp")
    admin = auth_header(role="admin", username="admin_stp")
    created = client.post("/api/v1/reviews", json=valid_case(), headers=maker).json()
    assert created["status"] == "auto_decided"

    blocked = client.post(
        f"/api/v1/reviews/{created['id']}/decision",
        json={"human_decision": "DENIED", "human_notes": "disagree"},
        headers=checker,
    )
    assert blocked.status_code == 409
    assert "straight-through" in blocked.json()["detail"]

    reopened = client.post(f"/api/v1/reviews/{created['id']}/reopen", headers=admin).json()
    assert reopened["status"] == "pending_review"
    assert reopened["final_decision"] is None
    assert reopened["decision_source"] is None

    decided = client.post(
        f"/api/v1/reviews/{created['id']}/decision",
        json={"human_decision": "DENIED", "human_notes": "Overturned on review"},
        headers=checker,
    ).json()
    assert decided["final_decision"] == "DENY"
    assert decided["decision_source"] == "human"

def test_maker_cannot_check_their_own_case(client, auth_header):
    headers = auth_header(username="maker2")
    created = client.post("/api/v1/reviews", json=human_case(), headers=headers).json()

    response = client.post(
        f"/api/v1/reviews/{created['id']}/decision",
        json={"human_decision": "APPROVED", "human_notes": "Looks fine"},
        headers=headers,
    )
    assert response.status_code == 403
    assert "Segregation of duties" in response.json()["detail"]


def test_different_reviewer_can_record_decision_and_it_becomes_immutable(
    client, auth_header
):
    maker_headers = auth_header(username="maker3")
    checker_headers = auth_header(username="checker3")
    created = client.post(
        "/api/v1/reviews", json=human_case(), headers=maker_headers
    ).json()

    response = client.post(
        f"/api/v1/reviews/{created['id']}/decision",
        json={"human_decision": "APPROVED", "human_notes": "Confirmed medically necessary"},
        headers=checker_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reviewed"
    assert body["human_decision"] == "APPROVED"
    assert body["final_decision"] == "APPROVE"
    assert body["decision_source"] == "human"
    assert body["reviewed_by"] == "checker3"

    repeat = client.post(
        f"/api/v1/reviews/{created['id']}/decision",
        json={"human_decision": "DENIED", "human_notes": "Changed my mind"},
        headers=checker_headers,
    )
    assert repeat.status_code == 409


def test_admin_can_review_own_case_and_reopen_a_decided_case(client, auth_header):
    admin_headers = auth_header(role="admin", username="admin1")
    created = client.post(
        "/api/v1/reviews", json=human_case(), headers=admin_headers
    ).json()

    decided = client.post(
        f"/api/v1/reviews/{created['id']}/decision",
        json={"human_decision": "ESCALATED", "human_notes": "Needs clinician input"},
        headers=admin_headers,
    )
    assert decided.status_code == 200

    reviewer_headers = auth_header(username="reviewer_only")
    forbidden = client.post(
        f"/api/v1/reviews/{created['id']}/reopen", headers=reviewer_headers
    )
    assert forbidden.status_code == 403

    reopened = client.post(
        f"/api/v1/reviews/{created['id']}/reopen", headers=admin_headers
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "pending_review"
    assert reopened.json()["human_decision"] is None


def test_list_reviews_is_paginated_and_filterable(client, auth_header):
    headers = auth_header(username="maker4")
    for i in range(3):
        client.post(
            "/api/v1/reviews", json=valid_case(case_id=f"case-{i}"), headers=headers
        )

    response = client.get("/api/v1/reviews?page=1&page_size=2", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["total"] >= 3

    pending = client.get("/api/v1/reviews?status=pending_review", headers=headers)
    assert pending.status_code == 200
    assert all(item["status"] == "pending_review" for item in pending.json()["items"])

    auto = client.get("/api/v1/reviews?status=auto_decided", headers=headers)
    assert auto.status_code == 200
    assert auto.json()["total"] >= 3
    assert all(item["decision_source"] == "stp" for item in auto.json()["items"])
