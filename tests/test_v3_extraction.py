"""Hybrid extraction tests with a mocked Ollama client."""
import io
import json
import os

import pytest
from PIL import Image

from backend import extraction, files, llm

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "sample_receipts")


def _bytes(name):
    with open(os.path.join(SAMPLES, name), "rb") as f:
        return f.read()


VLM_JSON = {
    "merchant_name": "Supermart Retail", "merchant_address": "123 Market St, Indiranagar", "date": "2026-10-18",
    "time": None, "invoice_number": "INV-84920", "currency": "INR",
    "items": [
        {"name": "Basmati Rice 5kg", "amount": 340.0, "category_hint": "Food"},
        {"name": "Sunflower Oil 1L", "amount": 165.0, "category_hint": "Food"},
        {"name": "Amul Butter 500g", "amount": 275.0, "category_hint": "Food"},
        {"name": "Tata Tea Gold 500g", "amount": 290.0, "category_hint": "Drinks"},
        {"name": "Colgate Toothpaste", "amount": 95.0, "category_hint": "Personal Care"},
        {"name": "Dettol Handwash", "amount": 120.0, "category_hint": "Personal Care"},
    ],
    "subtotal": 1285.0, "tax": 64.25, "total": 1349.25, "payment_method": None,
}


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda *a, **k: True)


def test_vlm_valid_json(online, monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps(VLM_JSON))
    out = extraction.extract(_bytes("sample_grocery.png"))
    assert out["engine_used"] == "vlm+ocr"
    assert out["fallback_reason"] is None
    assert out["extraction"]["merchant_name"] == "SUPERMART RETAIL"  # casing taken from OCR
    assert out["extraction"]["total"] == 1349.25
    assert len(out["items"]) == 6
    assert out["reconciliation"]["status"] == "match"
    assert all("category" in i and "category_confidence" in i for i in out["items"])


def test_vlm_invalid_json_falls_back(online, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", lambda *a, **k: calls.append(1) or "not json")
    out = extraction.extract(_bytes("sample_grocery.png"))
    assert len(calls) == 2  # one retry
    assert out["engine_used"] == "tesseract"
    assert "failed" in out["fallback_reason"]
    assert len(out["items"]) == 6


def test_offline_falls_back():
    out = extraction.extract(_bytes("sample_grocery.png"))
    assert out["engine_used"] == "tesseract"
    assert "not available" in out["fallback_reason"]
    assert out["extraction"]["merchant_name"] == "SUPERMART RETAIL"
    assert out["extraction"]["currency"] == "INR"


def test_hallucinated_number_flagged(online, monkeypatch):
    bad = json.loads(json.dumps(VLM_JSON))
    bad["items"][0]["amount"] = 999.99
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps(bad))
    out = extraction.extract(_bytes("sample_grocery.png"))
    assert out["items"][0]["low_confidence"] is True
    assert out["reconciliation"]["status"] == "mismatch"


def test_pdf_two_pages():
    img = Image.open(os.path.join(SAMPLES, "sample_grocery.png")).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PDF", save_all=True, append_images=[img.copy()])
    loaded = files.load_pages(buf.getvalue())
    assert loaded.ext == "pdf"
    assert len(loaded.pages) == 2


def test_rejects_unknown_type():
    with pytest.raises(files.UploadError):
        files.load_pages(b"GIF89a....")


def test_reconcile_tolerance():
    assert extraction.reconcile(1285.0, 1285.0, 64.25, 1349.25)["status"] == "match"
    assert extraction.reconcile(1000.0, None, None, 1009.0)["status"] == "match"  # within 1%
    assert extraction.reconcile(1000.0, None, None, 1020.0)["status"] == "mismatch"
    assert extraction.reconcile(10.0, None, None, None)["status"] == "unverified"
