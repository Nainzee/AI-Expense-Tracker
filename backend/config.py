"""Environment-driven settings for the v3 API. Read lazily so tests can override env vars."""

from __future__ import annotations

import os

VERSION = "3.0.0"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_PDF_PAGES = 5
MAX_STATEMENT_PAGES = 50
MAX_STATEMENT_ROWS = 5000
PREVIEW_TTL_S = 1800
TOKEN_TTL_HOURS = 12
CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def auth_disabled() -> bool:
    return env("AUTH_DISABLED", "0").lower() in ("1", "true", "yes")


def app_username() -> str:
    return env("APP_USERNAME")


def app_password() -> str:
    return env("APP_PASSWORD")


def secret_key() -> str:
    return env("APP_SECRET_KEY")


def ollama_url() -> str:
    return env("OLLAMA_URL", "http://localhost:11434").rstrip("/")


def ollama_vlm() -> str:
    return env("OLLAMA_VLM", "qwen2.5vl:7b")


def ollama_text_model() -> str:
    return env("OLLAMA_TEXT_MODEL", "qwen3:4b-instruct")


def ollama_timeout() -> float:
    try:
        return float(env("OLLAMA_TIMEOUT_S", "120"))
    except ValueError:
        return 120.0
