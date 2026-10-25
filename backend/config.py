"""Centralized, environment-driven application configuration.

Production deployments must override the defaults below via environment
variables (or a mounted .env file) rather than editing this file. Secrets
(JWT_SECRET_KEY, DATABASE_URL credentials) must never be committed to
source control.
"""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAIMLAB_", extra="ignore")

    environment: Literal["development", "staging", "production"] = "development"

    # SQLite is a reasonable local/dev default; production deployments should
    # set CLAIMLAB_DATABASE_URL to a real PostgreSQL DSN (see compose.yaml).
    database_url: str = "sqlite:///./data/claimlab.db"

    # JWT signing. The default is only usable for local development — it is
    # intentionally obvious/insecure so nobody mistakes it for a real secret.
    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 30

    # Requests per client IP per rolling window, applied to write endpoints.
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Registration is disabled by default; accounts are provisioned out of
    # band (e.g. by an admin script) so this demo cannot self-issue accounts
    # that could be mistaken for verified reviewers.
    allow_self_registration: bool = False

    # Multi-agent pipeline routing (Agent 5). A draft APPROVE/DENY is only
    # finalised via Straight-Through Processing when its calibrated confidence
    # meets the decision-specific threshold; denials require stronger
    # confidence than approvals. Everything else goes to the human queue.
    stp_enabled: bool = True
    stp_approve_threshold: float = 0.90
    stp_deny_threshold: float = 0.95

    # Gemini assistance inside the pipeline (Agent 1 clause parsing, Agent 5
    # audit-brief prose). Only effective when CAF_MODEL_API_KEY is set; the
    # deterministic output is always the fallback.
    pipeline_llm_assist: bool = True

    # Knowledge layer adapters (Agents 2 and 3). The defaults need no external
    # services; set these to use real Qdrant / Neo4j / SBioBERT.
    embedder: Literal["hashing", "sbiobert"] = "hashing"
    sbiobert_model: str = "pritamdeka/S-BioBert-snli-multinli-stsb"
    vector_backend: Literal["memory", "qdrant"] = "memory"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection_prefix: str = "claimlab"
    graph_backend: Literal["memory", "neo4j"] = "memory"
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None

    # Public demo (e.g. a hosted website). When enabled and a demo password is
    # set, two shared reviewer accounts (demo-maker / demo-checker) are seeded
    # on startup and their credentials are shown on the login screen so
    # visitors can try the maker-checker workflow. Never use for real data;
    # pair with the nightly reset in deploy/reset-demo.sh.
    public_demo: bool = False
    demo_password: str | None = None
    demo_notice: str = "Shared public demo: synthetic data only, visible to all visitors, reset every night."

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        return not self.is_production

    def require_non_default_secret(self) -> None:
        if self.is_production and self.jwt_secret_key == "dev-only-insecure-secret-change-me":
            raise RuntimeError(
                "Refusing to start in production with the default JWT secret. "
                "Set CLAIMLAB_JWT_SECRET_KEY to a strong, unique value."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
