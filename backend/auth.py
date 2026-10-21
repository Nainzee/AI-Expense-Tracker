"""Single-user auth: credentials from env, HS256 JWT bearer tokens."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend import config

_bearer = HTTPBearer(auto_error=False)


def auth_configured() -> bool:
    return bool(config.app_username() and config.app_password() and config.secret_key())


def check_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.encode(), config.app_username().encode())
    pass_ok = hmac.compare_digest(password.encode(), config.app_password().encode())
    return user_ok and pass_ok


def create_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(hours=config.TOKEN_TTL_HOURS)}
    return jwt.encode(payload, config.secret_key(), algorithm="HS256")


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if config.auth_disabled():
        return "dev"
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                                 headers={"WWW-Authenticate": "Bearer"})
    if not auth_configured() or creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, config.secret_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise unauthorized
    return str(payload.get("sub", ""))
