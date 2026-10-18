"""
Test suite for Phase 4: Optional Differentiators.
Tests:
1. Bank / UPI Statement CSV parsing and normalization.
2. Multi-factor receipt-to-statement transaction matching.
3. Statement reconciliation precision and recall benchmark.
4. Category budget threshold alerts.
5. Calendar month-end run-rate spend forecasting.
"""

import pytest
from statement_matcher import (
    BankTransaction,
    parse_bank_statement_csv,
    match_receipts_to_statement,
    evaluate_statement_matcher_benchmark,
    token_jaccard_similarity
)
from monthly_summary import (
    calculate_budget_alerts,
    calculate_month_end_forecast,
    DEFAULT_CATEGORY_BUDGETS
)


SAMPLE_STATEMENT_CSV = """Date,Narration,Withdrawal,Deposit,Balance
19-10-2026,POS/SUPERMART RETAIL/IND,1349.25,,45200.00
17-10-2026,UPI/APOLLO PHARMACY/29401,465.36,,46549.25
18-10-2026,CARD/ARTISAN CAFE BISTRO,1239.00,,47014.61
15-10-2026,IMPS/DIGITAL TECH HUB/58291,2591.28,,48253.61
10-10-2026,UPI/SWIGGY/FOOD-ORDER,350.00,,50844.89
"""


def test_parse_bank_statement_csv():
    """Verify parsing of bank statement CSV into structured BankTransaction objects."""
    txns = parse_bank_statement_csv(SAMPLE_STATEMENT_CSV)
    assert len(txns) == 5
    assert txns[0].amount == 1349.25
    assert "SUPERMART" in txns[0].description
    assert txns[0].date == "2026-10-19"


def test_match_receipts_to_statement():
    """Verify multi-factor reconciliation between receipts and transactions."""
    txns = parse_bank_statement_csv(SAMPLE_STATEMENT_CSV)
    receipts = [
        {"id": "REC-01", "store": "SUPERMART RETAIL", "date": "2026-10-18", "total": 1349.25},
        {"id": "REC-02", "store": "APOLLO MEDPLUS PHARMACY", "date": "2026-10-17", "total": 465.36},
        {"id": "REC-03", "store": "UNKNOWN VENDOR", "date": "2026-10-01", "total": 9999.00},
    ]

    matches = match_receipts_to_statement(receipts, txns, days_window=3, amt_tolerance=1.0)
    assert len(matches) == 3

    # Match 1: Supermart (date 18th vs statement 19th -> 1 day diff, exact amount)
    assert matches[0].match_status == "Verified Match"
    assert matches[0].txn_amount == 1349.25

    # Match 2: Apollo Pharmacy
    assert matches[1].match_status == "Verified Match"
    assert matches[1].txn_amount == 465.36

    # Match 3: Unmatched vendor
    assert matches[2].match_status == "Unmatched"


def test_statement_matcher_benchmark():
    """Verify precision and recall metrics of the matching engine."""
    res = evaluate_statement_matcher_benchmark()
    assert res["precision"] >= 75.0
    assert res["recall"] >= 85.0
    assert res["f1"] >= 80.0


def test_category_budget_alerts():
    """Verify category budget threshold alerting logic."""
    custom_budgets = {
        "Food": 1000.0,
        "Dining": 500.0,
        "Household": 2000.0
    }
    spend = {
        "Food": 1250.0,    # 125% -> Exceeded
        "Dining": 420.0,    # 84% -> Warning
        "Household": 600.0  # 30% -> On Track
    }

    alerts = calculate_budget_alerts(spend, budgets=custom_budgets, currency="₹")
    alert_map = {a["category"]: a for a in alerts}

    assert alert_map["Food"]["status"] == "🔴 Exceeded"
    assert alert_map["Dining"]["status"] == "🟡 Warning"
    assert alert_map["Household"]["status"] == "🟢 On Track"


def test_month_end_forecast():
    """Verify calendar-aware month-end forecasting."""
    sample_data = {
        "month": "2026-10",
        "grand_total": 3000.0,
        "receipts": [
            {"date": "2026-10-05", "total": 1000.0},
            {"date": "2026-10-15", "total": 2000.0}
        ],
        "currency": "₹"
    }

    forecast = calculate_month_end_forecast(sample_data)
    assert forecast["days_in_month"] == 31  # October has 31 days
    assert forecast["day_progress"] == 15
    assert forecast["daily_velocity"] == 200.0  # 3000 / 15
    assert forecast["projected_total"] == 6200.0  # 200 * 31
