"""Shared pytest fixtures.

Sets an isolated, file-based SQLite test database *before* any backend
module is imported, so tests never touch the real data/claimlab.db used by
local development. Pytest always imports conftest.py in a directory before
collecting test modules there, which is what makes this ordering safe.
"""
import os
import tempfile
import uuid
from pathlib import Path

_TEST_DB = Path(tempfile.gettempdir()) / f"claimlab_test_{uuid.uuid4().hex}.db"
os.environ["CLAIMLAB_DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("CLAIMLAB_ENVIRONMENT", "development")
# The full suite makes far more than the default 60 req/min from a single
# TestClient "IP"; raise the ceiling so unrelated tests never trip the rate
# limiter. test_rate_limit.py exercises the real limiter in isolation with
# its own small, explicit limit instead of relying on this default.
os.environ.setdefault("CLAIMLAB_RATE_LIMIT_REQUESTS", "100000")

import pytest
from fastapi.testclient import TestClient

from backend import models  # noqa: F401  (register models on Base.metadata)
from backend.db import Base, SessionLocal, engine
from backend.main import app
from backend.models import User
from backend.security import hash_password

Base.metadata.create_all(bind=engine)

DEFAULT_TEST_PASSWORD = "Sup3rSecretPassw0rd!"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def make_user():
    def _make(username: str, role: str = "reviewer", password: str = DEFAULT_TEST_PASSWORD):
        db = SessionLocal()
        try:
            user = User(
                username=username,
                hashed_password=hash_password(password),
                role=role,
                is_active=True,
            )
            db.add(user)
            db.commit()
        finally:
            db.close()
        return username, password

    return _make


@pytest.fixture
def auth_header(client: TestClient, make_user):
    def _header(role: str = "reviewer", username: str | None = None) -> dict[str, str]:
        username = username or f"user_{uuid.uuid4().hex[:8]}"
        uname, password = make_user(username, role=role)
        response = client.post(
            "/api/v1/auth/login", data={"username": uname, "password": password}
        )
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _header
