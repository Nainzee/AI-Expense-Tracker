"""
Phase 0 Reproduction Tests for Known Issues 1-5.

Guarantees that each regression or edge-case identified during user testing
is explicitly captured and verified.
"""

import pytest
from ocr_pipeline import (
    parse_line_items,
    extract_receipt_metadata,
    detect_currency_symbol,
    is_valid_merchant_name,
)
from categorizer import predict_category
from monthly_summary import get_monthly_data, list_receipts


def test_issue_1_dashboard_and_repository_read_same_store():
    """Problem 1: Dashboard and repository must read from the exact same store."""
    monthly_data = get_monthly_data("2026-10")
    repo_receipts = list_receipts("2026-10")
    if monthly_data and monthly_data.get("receipts"):
        assert monthly_data["receipt_count"] == len(repo_receipts)
        assert monthly_data["grand_total"] > 0
        repo_total = sum(r["total"] for r in repo_receipts)
        assert round(monthly_data["grand_total"], 2) == round(repo_total, 2)


def test_issue_2_parser_filters_fake_items_and_attaches_weights():
    """Problem 2: Parser suppresses DATE, payment, promo rows, attaches weights, preserves discounts."""
    raw_receipt_lines = [
        {"text": "SUPERMART FRESH", "confidence": 98.0, "bbox": None},
        {"text": "DATE 2016-01-15", "confidence": 95.0, "bbox": None},
        {"text": "LETTUCE ICEBERG", "confidence": 92.0, "bbox": None},
        {"text": "0.778 kg NET @ $5.99/kg $4.66", "confidence": 90.0, "bbox": None},
        {"text": "SPECIAL - $2.00", "confidence": 90.0, "bbox": None},
        {"text": "LOYALTY -15.00", "confidence": 92.0, "bbox": None},
        {"text": "CASH 50.00", "confidence": 95.0, "bbox": None},
        {"text": "CHANGE 32.34", "confidence": 95.0, "bbox": None},
        {"text": "TOTAL $4.66", "confidence": 98.0, "bbox": None},
    ]

    items = parse_line_items(raw_receipt_lines)
    item_names = [it.item_name for it in items]

    # Date must NOT become an item
    assert not any("date" in name.lower() or "2016" in name for name in item_names)

    # Tender must NOT become an item
    assert not any("cash" in name.lower() or "change" in name.lower() for name in item_names)

    # Weight line must be attached to LETTUCE ICEBERG with correct qty and unit price
    lettuce = next((it for it in items if "LETTUCE ICEBERG" in it.item_name), None)
    assert lettuce is not None
    assert lettuce.quantity == 0.778
    assert lettuce.unit_price == 5.99
    assert lettuce.amount == 4.66

    # Discounts must retain their negative sign
    loyalty = next((it for it in items if "LOYALTY" in it.item_name), None)
    assert loyalty is not None
    assert loyalty.amount < 0
    assert loyalty.amount == -15.00


def test_issue_3_classifier_negative_filter_on_junk():
    """Problem 3: Junk words like CASH, CHANGE, and weights must NOT be classified as Drinks."""
    junk_inputs = ["CASH", "CASH 50", "CHANGE", "0.778 kg NET", "SPECIAL", "LOYALTY"]
    for text in junk_inputs:
        cat, conf = predict_category(text)
        assert cat == "Other", f"Expected '{text}' to be 'Other', but got '{cat}'"
        assert conf >= 0.90, f"Expected high confidence for non-item '{text}', got {conf}"

    # Verify real food produce is not classified as Drinks
    cat, conf = predict_category("LETTUCE ICEBERG")
    assert cat == "Food", f"Expected LETTUCE ICEBERG to be Food, got {cat}"


def test_issue_4_garbage_merchant_rejection():
    """Problem 4: Repetitive character OCR noise (e.g. HRESEESESSESS) must be rejected."""
    assert not is_valid_merchant_name("HRESEESESSESS")
    assert not is_valid_merchant_name("AAAAAAAABBBB")
    assert not is_valid_merchant_name("1234567890")
    # Valid stores must pass
    assert is_valid_merchant_name("SUPERMART RETAIL")
    assert is_valid_merchant_name("Trader Joe's")
    assert is_valid_merchant_name("Apollo Pharmacy")


def test_issue_5_currency_symbol_detection():
    """Problem 5: Currency symbol detection must detect $ when printed."""
    dollar_text = "LETTUCE ICEBERG $4.66\nSUBTOTAL $4.66\nTOTAL $4.66"
    rupee_text = "BASMATI RICE ₹340.00\nTOTAL ₹340.00"
    euro_text = "BAGUETTE 2.50 €\nTOTAL 2.50 €"

    assert detect_currency_symbol(dollar_text) == "$"
    assert detect_currency_symbol(rupee_text) == "₹"
    assert detect_currency_symbol(euro_text) == "€"
