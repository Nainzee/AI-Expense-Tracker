"""Entrypoint shim: `uvicorn api:app`. The application lives in backend/main.py."""

from backend.main import app  # noqa: F401
