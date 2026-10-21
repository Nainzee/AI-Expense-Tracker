from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend import auth, config

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=200)


@router.post("/login")
def login(body: LoginRequest):
    if config.auth_disabled():
        return {"access_token": auth.create_token(body.username or "dev") if config.secret_key() else "dev",
                "token_type": "bearer"}
    if not auth.auth_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Login is not configured. Set APP_USERNAME, APP_PASSWORD and APP_SECRET_KEY.")
    if not auth.check_credentials(body.username, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    return {"access_token": auth.create_token(body.username), "token_type": "bearer"}


@router.get("/me")
def me(user: str = Depends(auth.require_user)):
    return {"username": user}
