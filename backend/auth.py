"""Authentication dependencies and the login route.

Uses the standard OAuth2 "password" flow (username + password -> bearer
token) so it works with FastAPI's interactive docs "Authorize" button and any
standard OAuth2 client. Accounts are provisioned via scripts/create_user.py;
self-registration is disabled by default (see backend.config.Settings).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import User
from backend.schemas import TokenResponse, UserPublic
from backend.security import create_access_token, decode_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise _unauthorized("Not authenticated")
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise _unauthorized("Invalid or expired token") from exc
    username = payload.get("sub")
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive")
    return user


def require_role(*roles: str):
    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return user

    return _check


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = db.query(User).filter(User.username == form_data.username).one_or_none()
    if user is None or not user.is_active or not verify_password(
        form_data.password, user.hashed_password
    ):
        raise _unauthorized("Incorrect username or password")
    token = create_access_token(subject=user.username, role=user.role)
    return TokenResponse(access_token=token, token_type="bearer", role=user.role)


@router.get("/me", response_model=UserPublic)
def read_current_user(user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic(username=user.username, role=user.role, is_active=user.is_active)
