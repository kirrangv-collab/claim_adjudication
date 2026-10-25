"""Public demo mode: seeded accounts, nightly reset and /config exposure."""
import importlib.util
import sys
from pathlib import Path

import pytest

from backend import main
from backend.db import SessionLocal
from backend.models import CaseReview, User

_spec = importlib.util.spec_from_file_location(
    "seed_demo", Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"
)
seed_demo = importlib.util.module_from_spec(_spec)
sys.modules["seed_demo"] = seed_demo
_spec.loader.exec_module(seed_demo)

DEMO_PASSWORD = "try-claimlab-demo"


def _case():
    return {
        "diagnosis": "glaucoma",
        "requested_service": "glaucoma imaging",
        "policy_text": "Glaucoma imaging is covered when medically necessary.",
        "clinical_evidence": "Documented glaucoma imaging is medically necessary.",
        "evidence_state": "incomplete",
    }


@pytest.fixture
def demo_settings(monkeypatch):
    monkeypatch.setattr(main.settings, "public_demo", True)
    monkeypatch.setattr(main.settings, "demo_password", DEMO_PASSWORD)


def test_config_hides_demo_info_unless_enabled(client, monkeypatch):
    monkeypatch.setattr(main.settings, "public_demo", False)
    assert client.get("/api/v1/config").json()["public_demo"] is None
    monkeypatch.setattr(main.settings, "public_demo", True)
    monkeypatch.setattr(main.settings, "demo_password", None)
    assert client.get("/api/v1/config").json()["public_demo"] is None


def test_config_exposes_demo_accounts_when_enabled(client, demo_settings):
    demo = client.get("/api/v1/config").json()["public_demo"]
    assert [a["username"] for a in demo["accounts"]] == ["demo-maker", "demo-checker"]
    assert {a["role"] for a in demo["accounts"]} == {"reviewer"}
    assert demo["password"] == DEMO_PASSWORD
    assert "synthetic data only" in demo["notice"]


def test_seed_is_idempotent_and_never_creates_admins(client):
    db = SessionLocal()
    try:
        first = seed_demo.seed_demo_accounts(db, DEMO_PASSWORD)
        second = seed_demo.seed_demo_accounts(db, DEMO_PASSWORD)
        users = db.query(User).filter(User.username.in_(["demo-maker", "demo-checker"])).all()
    finally:
        db.close()
    assert all(a.startswith(("created", "updated")) for a in first)
    assert second == ["updated demo-maker", "updated demo-checker"]
    assert len(users) == 2 and {u.role for u in users} == {"reviewer"}
    with pytest.raises(ValueError):
        seed_demo.seed_demo_accounts(SessionLocal(), "short")


def test_demo_accounts_can_run_the_maker_checker_flow_and_reset_wipes_reviews(client, auth_header):
    db = SessionLocal()
    try:
        seed_demo.seed_demo_accounts(db, DEMO_PASSWORD)
    finally:
        db.close()

    def login(username):
        response = client.post("/api/v1/auth/login", data={"username": username, "password": DEMO_PASSWORD})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    maker, checker = login("demo-maker"), login("demo-checker")
    admin = auth_header(role="admin", username="demo_reset_admin")
    created = client.post("/api/v1/reviews", json=_case(), headers=maker).json()
    assert created["status"] == "pending_review"
    decided = client.post(f"/api/v1/reviews/{created['id']}/decision",
                          json={"human_decision": "ESCALATED", "human_notes": "demo"}, headers=checker)
    assert decided.status_code == 200

    db = SessionLocal()
    try:
        reviews, _ = seed_demo.reset_demo_data(db)
        remaining = db.query(CaseReview).count()
        admin_still_exists = db.query(User).filter(User.username == "demo_reset_admin").count()
    finally:
        db.close()
    assert reviews >= 1 and remaining == 0
    assert admin_still_exists == 1
    assert client.get("/api/v1/reviews", headers=admin).json()["total"] == 0
