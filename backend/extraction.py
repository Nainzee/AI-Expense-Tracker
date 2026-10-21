"""Hybrid receipt extraction: local VLM (Ollama) guided by Tesseract OCR, with Tesseract-only fallback."""

from __future__ import annotations

import json
import logging
import re
from datetime import date as date_cls, datetime, timedelta
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

import storage
from backend import config, files, llm

log = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6
_DISCOUNT_RE = re.compile(r"disc|off\b|coupon|promo|saving|less|voucher|cashback", re.I)


# ─── Structured output schema for the VLM ───────────────────────────────────
class ExtractedLineItem(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: float = Field(description="Final line amount as printed (negative only for discounts)")
    discount: Optional[float] = None
    category_hint: Optional[str] = None


class ReceiptExtraction(BaseModel):
    merchant_name: Optional[str] = None
    merchant_address: Optional[str] = None
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    time: Optional[str] = Field(default=None, description="HH:MM 24h")
    invoice_number: Optional[str] = None
    currency: Optional[str] = Field(default=None, description="ISO 4217 code, e.g. INR, USD")
    items: List[ExtractedLineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    payment_method: Optional[str] = None


def _system_prompt() -> str:
    from categorizer import CANONICAL_CATEGORIES

    return (
        "You extract structured data from shopping receipts and bills. Respond with JSON only, matching the schema.\n"
        "Rules:\n"
        "- items: only purchased products/services. Never include subtotal, tax, total, payment, tender, change or "
        "loyalty lines as items.\n"
        "- amount is the final printed amount for the line (after quantity). Discounts are separate items with a "
        "negative amount.\n"
        "- Copy names and numbers exactly as printed; the OCR text is a hint for exact digits but the image is the "
        "source of truth. Use null for anything not printed; never invent values.\n"
        "- merchant_name exactly as printed at the top of the receipt.\n"
        "- date as YYYY-MM-DD, time as HH:MM (24h), currency as an ISO 4217 code (Rs/₹ means INR).\n"
        f"- category_hint must be one of: {', '.join(CANONICAL_CATEGORIES)}."
    )


# ─── Tesseract path ─────────────────────────────────────────────────────────
def _run_tesseract(pages) -> Tuple[List[dict], dict, str]:
    from ocr_pipeline import extract_receipt_items

    all_items: List[dict] = []
    meta: dict = {}
    texts: List[str] = []
    for page in pages:
        items, raw_text, page_meta, _ = extract_receipt_items(page)
        all_items.extend(items)
        texts.append(raw_text)
        for k, v in page_meta.items():
            if v not in (None, "", 0.0) and not meta.get(k):
                meta[k] = v
    return all_items, meta, "\n".join(texts)


def _run_vlm(pages, ocr_text: str) -> ReceiptExtraction:
    images = [llm.image_to_b64(p) for p in pages]
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": f"OCR text (may contain errors):\n{ocr_text[:6000]}\n\nExtract the receipt.",
         "images": images},
    ]
    last_exc: Exception = RuntimeError("no attempt")
    for _ in range(2):  # one retry
        try:
            content = llm.chat(config.ollama_vlm(), messages, schema=ReceiptExtraction.model_json_schema())
            return ReceiptExtraction.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError, llm.LLMError) as exc:
            last_exc = exc
            log.warning("VLM extraction attempt failed: %s", exc)
    raise last_exc


# ─── Validation helpers ─────────────────────────────────────────────────────
def _numbers_in(text: str) -> set:
    found = set()
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            found.add(round(float(tok.replace(",", "")), 2))
        except ValueError:
            pass
    return found


def _valid_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            d = datetime.strptime(value.strip(), fmt).date()
            break
        except ValueError:
            continue
    else:
        return None
    if d < date_cls(2000, 1, 1) or d > date_cls.today() + timedelta(days=1):
        return None
    return d.isoformat()


def reconcile(items_sum: float, subtotal: Optional[float], tax: Optional[float], total: Optional[float]) -> dict:
    """Compare the line-item sum with printed subtotal/total; tolerance max(1.5, 1%)."""
    refs = []
    if subtotal:
        refs.append(("subtotal", subtotal))
    if total:
        refs.append(("total", total))
        if tax:
            refs.append(("total_minus_tax", round(total - tax, 2)))
    if not refs:
        return {"status": "unverified", "items_sum": round(items_sum, 2), "reference": None, "difference": None}
    label, ref = min(refs, key=lambda r: abs(items_sum - r[1]))
    diff = round(items_sum - ref, 2)
    tol = max(1.5, abs(ref) * 0.01)
    return {"status": "match" if abs(diff) <= tol else "mismatch", "items_sum": round(items_sum, 2),
            "reference": label, "difference": diff}


def _categorize(items: List[dict], hints: List[Optional[str]]) -> List[dict]:
    from categorizer import CANONICAL_CATEGORIES, categorize_items

    categorized = categorize_items([{"item_name": i["item_name"], "amount": i["amount"]} for i in items])
    out = []
    for item, cat, hint in zip(items, categorized, hints):
        category, conf, source = cat["category"], float(cat.get("confidence", 0.0)), "model"
        if conf < LOW_CONFIDENCE_THRESHOLD and hint in CANONICAL_CATEGORIES:
            category, conf, source = hint, LOW_CONFIDENCE_THRESHOLD, "llm_hint"
        out.append({**item, "category": category, "category_confidence": round(conf, 3),
                    "category_source": source,
                    "low_confidence": item.get("low_confidence", False) or conf < LOW_CONFIDENCE_THRESHOLD})
    return out


# ─── Main entry ─────────────────────────────────────────────────────────────
def extract(data: bytes) -> dict:
    """Run the hybrid pipeline on raw upload bytes. Raises files.UploadError for bad input."""
    loaded = files.load_pages(data)
    ocr_items, ocr_meta, ocr_text = _run_tesseract(loaded.pages)
    hint_text = loaded.pdf_text or ocr_text

    vlm: Optional[ReceiptExtraction] = None
    fallback_reason: Optional[str] = None
    if llm.is_available(config.ollama_vlm()):
        try:
            vlm = _run_vlm(loaded.pages, hint_text)
            if not vlm.items:
                fallback_reason, vlm = "VLM returned no line items", None
        except Exception as exc:  # any LLM failure -> OCR fallback
            fallback_reason = f"VLM extraction failed: {type(exc).__name__}"
    else:
        fallback_reason = "Local AI model (Ollama) is not available"

    hints: List[Optional[str]] = []
    if vlm:
        engine = "vlm+ocr"
        ocr_store = ocr_meta.get("store_name") or ""
        merchant = vlm.merchant_name or ocr_store
        if ocr_store and merchant and ocr_store.lower() == merchant.lower():
            merchant = ocr_store  # keep the casing as printed/recognised
        extraction = {
            "merchant_name": merchant, "merchant_address": vlm.merchant_address, "date": vlm.date,
            "time": vlm.time, "invoice_number": vlm.invoice_number,
            "currency": storage.to_iso_currency(vlm.currency or ocr_meta.get("currency")),
            "subtotal": vlm.subtotal, "tax": vlm.tax,
            "total": vlm.total if vlm.total is not None else ocr_meta.get("grand_total"),
            "payment_method": vlm.payment_method,
        }
        items = []
        for it in vlm.items:
            items.append({"item_name": it.name.strip(), "amount": float(it.amount), "quantity": it.quantity or 1.0,
                          "unit_price": it.unit_price, "discount": it.discount or 0.0})
            hints.append(it.category_hint)
    else:
        engine = "tesseract"
        extraction = {
            "merchant_name": ocr_meta.get("store_name") or "", "merchant_address": None,
            "date": ocr_meta.get("date") or None, "time": None,
            "invoice_number": ocr_meta.get("invoice_number") or None,
            "currency": storage.to_iso_currency(ocr_meta.get("currency")),
            "subtotal": ocr_meta.get("subtotal"), "tax": ocr_meta.get("tax"),
            "total": ocr_meta.get("grand_total"), "payment_method": ocr_meta.get("payment_method") or None,
        }
        items = [{"item_name": i["item_name"], "amount": float(i["amount"]), "quantity": i.get("quantity") or 1.0,
                  "unit_price": i.get("unit_price"), "discount": 0.0} for i in ocr_items]
        hints = [None] * len(items)

    # ── Post-validation ──
    low_fields: List[str] = []
    known_numbers = _numbers_in(hint_text) if hint_text.strip() else None
    for item in items:
        item["low_confidence"] = False
        if item["amount"] < 0 and not _DISCOUNT_RE.search(item["item_name"]):
            item["amount"] = abs(item["amount"])
            item["low_confidence"] = True
        if known_numbers is not None and round(abs(item["amount"]), 2) not in known_numbers:
            item["low_confidence"] = True

    raw_date = extraction["date"]
    extraction["date"] = _valid_date(raw_date)
    if not extraction["date"]:
        low_fields.append("date")
    if not extraction["merchant_name"]:
        low_fields.append("merchant_name")
    for field in ("subtotal", "tax", "total"):
        val = extraction[field]
        if val is not None and known_numbers is not None and round(val, 2) not in known_numbers:
            low_fields.append(field)

    items_sum = sum(i["amount"] for i in items)
    if extraction["total"] is None:
        extraction["total"] = round(items_sum, 2)
        low_fields.append("total")
    recon = reconcile(items_sum, extraction["subtotal"], extraction["tax"], extraction["total"])

    return {
        "extraction": extraction,
        "items": _categorize(items, hints) if items else [],
        "low_confidence_fields": sorted(set(low_fields)),
        "engine_used": engine,
        "fallback_reason": fallback_reason,
        "reconciliation": recon,
        "raw_text": hint_text,
        "file_ext": loaded.ext,
    }
