"""Password hashing and JWT helpers.

Passwords are hashed with bcrypt directly (not passlib, to avoid its
bcrypt-backend version friction). Tokens are signed HS256 JWTs with a short
default lifetime; the secret must be overridden in any non-development
environment (enforced by Settings.require_non_default_secret).
"""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from backend.config import get_settings

ALGORITHM_ISSUE_WARNING = "HS256 only; do not switch to 'none' or asymmetric without review."


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def create_access_token(*, subject: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_minutes)
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
