"""
Test suite for Phase 3: Evaluation Suite Verification.
Ensures benchmark metrics, active learning simulation, and results artifacts are reproducible.
"""

import os
import json
import pytest

from eval.benchmark import run_benchmark, fuzzy_name_match
from eval.simulate_corrections import evaluate_accuracy


def test_ground_truth_integrity():
    """Verify ground truth database structure and required keys."""
    gt_path = "eval/ground_truth.json"
    assert os.path.exists(gt_path), "Missing eval/ground_truth.json"

    with open(gt_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) >= 4, "Ground truth should have at least 4 test receipts"
    for r_id, rec in data.items():
        assert "image_path" in rec
        assert "grand_total" in rec
        assert "items" in rec
        assert len(rec["items"]) > 0
        for it in rec["items"]:
            assert "item_name" in it
            assert "amount" in it
            assert "category" in it


def test_fuzzy_name_match():
    """Verify token Jaccard similarity utility."""
    sim_exact = fuzzy_name_match("Basmati Rice 5kg", "Basmati Rice 5kg")
    assert sim_exact == 1.0

    sim_partial = fuzzy_name_match("Basmati Rice 5kg", "Organic Basmati Rice 5kg")
    assert 0.5 <= sim_partial < 1.0

    sim_none = fuzzy_name_match("Basmati Rice", "USB-C Cable")
    assert sim_none == 0.0


def test_benchmark_execution():
    """Verify benchmark run produces valid metrics."""
    metrics = run_benchmark()
    assert metrics["item_recall"] >= 0.85
    assert metrics["item_precision"] >= 0.85
    assert metrics["total_match_rate"] >= 80.0
    assert metrics["cat_accuracy"] >= 0.75
    assert metrics["tess_median_ms"] > 0
    assert metrics["trocr_median_ms"] > 0

    assert os.path.exists("eval/results.md")
    assert os.path.exists("eval/failure_cases.md")
    assert os.path.exists("eval/correction_curve.png")
