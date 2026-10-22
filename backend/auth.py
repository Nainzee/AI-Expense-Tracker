"""Multi-user auth: database storage with PBKDF2-HMAC-SHA256, env fallback, HS256 JWT tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import storage
from backend import config

_bearer = HTTPBearer(auto_error=False)
_DEFAULT_JWT_KEY = "smart-expense-tracker-jwt-secret-key-3.0-secure"


def _jwt_secret() -> str:
    return config.secret_key() or _DEFAULT_JWT_KEY


def init_users_table() -> None:
    """Ensure the users table exists in SQLite database."""
    storage.init_db()
    with storage.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()


def hash_password(password: str) -> tuple[str, str]:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 rounds and random salt."""
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return pw_hash.hex(), salt.hex()


def verify_password(password: str, pw_hash: str, salt_hex: str) -> bool:
    """Verify password against stored PBKDF2 hex hash."""
    try:
        salt = bytes.fromhex(salt_hex)
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000).hex()
        return hmac.compare_digest(pw_hash, calc)
    except Exception:
        return False


def register_user(username: str, password: str) -> bool:
    """Register a new user in SQLite. Returns True on success, False if username exists."""
    init_users_table()
    u = username.strip()
    if not u:
        return False
    pw_hash, salt = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with storage.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (u, pw_hash, salt, now)
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def check_db_user(username: str, password: str) -> bool:
    """Check credentials against SQLite users table."""
    init_users_table()
    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),)
        ).fetchone()
        if not row:
            return False
        return verify_password(password, row["password_hash"], row["salt"])


def has_registered_users() -> bool:
    """Check if any users are registered in SQLite."""
    init_users_table()
    with storage.get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return bool(row and row[0] > 0)


def auth_configured() -> bool:
    """Check if single-user admin environment variables are configured."""
    return bool(config.app_username() and config.app_password() and config.secret_key())


def check_credentials(username: str, password: str) -> bool:
    """Check single-user credentials from environment variables."""
    user_ok = hmac.compare_digest(username.encode(), config.app_username().encode())
    pass_ok = hmac.compare_digest(password.encode(), config.app_password().encode())
    return user_ok and pass_ok


def create_token(username: str) -> str:
    """Generate a signed HS256 JWT bearer token."""
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(hours=config.TOKEN_TTL_HOURS)}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def require_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """FastAPI dependency: validate Bearer token and return username."""
    if config.auth_disabled():
        return "dev"
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                                 headers={"WWW-Authenticate": "Bearer"})
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise unauthorized
    sub = str(payload.get("sub", "")).strip()
    if not sub:
        raise unauthorized
    return sub
