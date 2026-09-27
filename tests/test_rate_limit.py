"""Isolated test of the rate-limiting middleware.

Uses its own tiny FastAPI app and a monkeypatched low limit so it never
depends on (or interferes with) the shared app's rate-limit configuration
used by the rest of the test suite (see conftest.py).
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.rate_limit import RateLimitMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_rate_limit_blocks_after_configured_threshold(monkeypatch):
    monkeypatch.setenv("CLAIMLAB_RATE_LIMIT_REQUESTS", "3")
    monkeypatch.setenv("CLAIMLAB_RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    try:
        client = TestClient(_build_app())
        statuses = [client.get("/ping").status_code for _ in range(5)]
        assert statuses == [200, 200, 200, 429, 429]

        limited = client.get("/ping")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        assert "Rate limit exceeded" in limited.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_rate_limit_tracks_clients_independently(monkeypatch):
    monkeypatch.setenv("CLAIMLAB_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("CLAIMLAB_RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    try:
        app = _build_app()
        client_a = TestClient(app, client=("10.0.0.1", 1234))
        client_b = TestClient(app, client=("10.0.0.2", 5678))

        assert client_a.get("/ping").status_code == 200
        assert client_a.get("/ping").status_code == 429
        # A different client IP has its own independent bucket.
        assert client_b.get("/ping").status_code == 200
    finally:
        get_settings.cache_clear()
