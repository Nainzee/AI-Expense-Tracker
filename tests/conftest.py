"""Shared test setup: isolated temp DB, auth disabled, Ollama treated as offline unless a test mocks it."""

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="receipts_test_")
os.environ["RECEIPTS_DB_PATH"] = os.path.join(_TMP, "receipts.db")
os.environ["AUTH_DISABLED"] = "1"
# Unroutable host so any un-mocked LLM call fails fast instead of hitting a real Ollama
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"
# Use locally cached Hugging Face models only; never block tests on the network
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _llm_offline(monkeypatch):
    """Default: LLM unavailable (tests that need it patch backend.llm functions)."""
    from backend import llm
    monkeypatch.setattr(llm, "is_available", lambda *a, **k: False)
    yield
