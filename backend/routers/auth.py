"""Authentication router: sign up, log in, and current session inspection."""

from __future__ import annotations

import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend import auth, config

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=4, max_length=200)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest):
    username = body.username.strip()
    if not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Username can only contain letters, numbers, dots, dashes, and underscores."
        )

    if not auth.register_user(username, body.password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Username '{username}' is already taken."
        )

    token = auth.create_token(username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": username,
    }


@router.post("/login")
def login(body: LoginRequest):
    username = body.username.strip()

    # 1. Dev mode override if auth is explicitly disabled
    if config.auth_disabled():
        user_name = username or "dev"
        return {
            "access_token": auth.create_token(user_name),
            "token_type": "bearer",
            "username": user_name,
        }

    # 2. Check registered users in SQLite
    if auth.check_db_user(username, body.password):
        return {
            "access_token": auth.create_token(username),
            "token_type": "bearer",
            "username": username,
        }

    # 3. Check environment-configured admin credentials
    if auth.auth_configured():
        if auth.check_credentials(username, body.password):
            return {
                "access_token": auth.create_token(username),
                "token_type": "bearer",
                "username": username,
            }
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    # 4. If users exist in database but password didn't match
    if auth.has_registered_users():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    # 5. Neither DB user nor env credentials configured
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Login is not configured. Please sign up to create an account."
    )


@router.get("/me")
def me(user: str = Depends(auth.require_user)):
    return {"username": user}
