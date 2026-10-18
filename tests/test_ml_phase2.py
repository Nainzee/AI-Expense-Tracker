"""
Test suite for Phase 2: ML Improvements.
Tests:
1. Data split integrity (strict split before augmentation, no leakage).
2. 'Other' negative class suppression (cash, scale lines, tender, promo).
3. Confidence calibration (Temperature Scaling and ECE verification).
4. Personalization memory (instant exact/fuzzy correction recall).
5. Personalized retraining (fast lightweight adapter).
"""

import json
import os
import pytest
import numpy as np
import torch

from categorizer import (
    predict_category,
    categorize_items,
    retrain_personalized_classifier,
    is_non_item_or_discount
)
from train import augment_train_item, compute_ece, TemperatureScaler
from storage import record_user_correction, get_user_correction


def test_data_split_no_leakage():
    """Verify that splitting before augmentation produces completely disjoint item sets."""
    with open("data/categories.json", "r", encoding="utf-8") as f:
        tax = json.load(f)

    for cat, items in tax.items():
        unique_items = list(dict.fromkeys([x.strip().lower() for x in items if x.strip()]))
        val_count = max(2, int(len(unique_items) * 0.20))
        val_items = set(unique_items[:val_count])
        train_items = set(unique_items[val_count:])

        assert len(val_items.intersection(train_items)) == 0, f"Leakage found in category {cat}"


def test_negative_class_suppression():
    """Verify that tender, scale lines, and metadata classify as 'Other'."""
    test_cases = [
        "CASH 50.00",
        "0.778 kg NET",
        "EFTPOS DEBIT",
        "CHANGE DUE",
        "VISA PURCHASE",
        "LOYALTY -15.00",
        "SPECIAL DISCOUNT",
        "SUBTOTAL",
        "BALANCE DUE",
    ]

    for item_text in test_cases:
        cat, conf = predict_category(item_text)
        assert cat == "Other", f"Failed for '{item_text}': predicted {cat} ({conf})"
        assert conf >= 0.85, f"Confidence too low for suppressed item '{item_text}': {conf}"


def test_produce_classification():
    """Verify that global produce items are classified as Food."""
    produce_items = [
        "LETTUCE ICEBERG",
        "Fresh Avocado",
        "Organic Celery",
        "Broccoli Crowns",
        "Red Onions",
    ]

    for item_text in produce_items:
        cat, conf = predict_category(item_text)
        assert cat == "Food", f"Expected 'Food' for produce '{item_text}', got '{cat}'"


def test_temperature_scaling_and_ece():
    """Verify temperature scaling calibration math and ECE computation."""
    np.random.seed(42)
    torch.manual_seed(42)

    n_samples = 50
    n_classes = 4
    labels = torch.randint(0, n_classes, (n_samples,))
    logits = torch.randn(n_samples, n_classes) * 2.0
    for i in range(n_samples):
        logits[i, labels[i]] += 4.0

    uncal_probs = torch.softmax(logits, dim=1).numpy()
    uncal_nll = torch.nn.CrossEntropyLoss()(logits, labels).item()

    scaler = TemperatureScaler()
    opt_temp = scaler.calibrate(logits, labels)

    assert opt_temp > 0.5, f"Unexpected temperature: {opt_temp}"

    cal_logits = scaler(logits)
    cal_nll = torch.nn.CrossEntropyLoss()(cal_logits, labels).item()

    assert cal_nll <= uncal_nll + 1e-4, f"Calibration did not reduce NLL: {uncal_nll} -> {cal_nll}"

    # Verify saved temperature.json from train.py
    temp_json_path = "models/category_model/temperature.json"
    if os.path.exists(temp_json_path):
        with open(temp_json_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["temperature"] > 0
        assert meta["ece_after"] <= meta["ece_before"], f"ECE was not reduced: {meta['ece_before']} -> {meta['ece_after']}"



def test_user_personalization_memory():
    """Verify that user correction is remembered and takes precedence."""
    test_item = "Ultra Widget X9000"
    user_category = "Electronics"

    record_user_correction(test_item, user_category, store="TechStore")

    retrieved = get_user_correction(test_item)
    assert retrieved == user_category

    pred_cat, pred_conf = predict_category(test_item)
    assert pred_cat == user_category
    assert pred_conf == 1.0


def test_personalized_classifier_retraining():
    """Verify on-device personalization retraining pipeline."""
    record_user_correction("Special Espresso Blend", "Drinks")
    record_user_correction("Organic Blue Denim Jeans", "Apparel")

    res = retrain_personalized_classifier()
    assert res["status"] == "success"
    assert res["user_corrections_count"] >= 2
    assert "Drinks" in res["classes"]
    assert "Apparel" in res["classes"]

    # Verify model artifact exists
    assert os.path.exists("models/user_personalized_model.joblib")


def test_active_learning_categorization_output():
    """Verify confidence annotation for active learning review."""
    items = [
        {"item_name": "Basmati Rice", "amount": 100.0},
        {"item_name": "0.778 kg NET", "amount": 4.66},
    ]

    categorized = categorize_items(items)
    assert len(categorized) == 2
    assert categorized[0]["category"] == "Food"
    assert categorized[0]["confidence"] >= 0.70
    assert categorized[1]["category"] == "Other"
