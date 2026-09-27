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
