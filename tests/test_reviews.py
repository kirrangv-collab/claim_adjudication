"""Tests for authentication and the human-review workflow.

These exercise the maker-checker segregation of duties, decision
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


def test_create_review_persists_prototype_suggestion_only(client, auth_header):
    headers = auth_header(username="maker1")
    response = client.post("/api/v1/reviews", json=valid_case(), headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["suggested_decision"] == "APPROVE"
    assert body["status"] == "pending_review"
    assert body["human_decision"] is None
    assert body["created_by"] == "maker1"


def test_maker_cannot_check_their_own_case(client, auth_header):
    headers = auth_header(username="maker2")
    created = client.post("/api/v1/reviews", json=valid_case(), headers=headers).json()

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
        "/api/v1/reviews", json=valid_case(), headers=maker_headers
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
        "/api/v1/reviews", json=valid_case(), headers=admin_headers
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
