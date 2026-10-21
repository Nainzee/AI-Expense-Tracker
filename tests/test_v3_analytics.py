"""Analytics + insights/chat tests (temp DB from conftest, mocked LLM)."""
import json
from datetime import date

import pytest

import storage
from backend import analytics, db, insights, llm


@pytest.fixture
def data():
    ids = [
        storage.save_receipt("Shop A", [{"item_name": "Rice", "amount": 300, "category": "Food"},
                                        {"item_name": "Soap", "amount": 100, "category": "Personal Care"}],
                             scan_date="2025-01-10", grand_total=400, currency="INR"),
        storage.save_receipt("Shop B", [{"item_name": "Pizza", "amount": 600, "category": "Dining"}],
                             scan_date="2025-02-05", grand_total=600, currency="₹"),
        storage.save_receipt("US Store", [{"item_name": "Cable", "amount": 20, "category": "Electronics"}],
                             scan_date="2025-02-06", grand_total=20, currency="USD"),
    ]
    yield ids
    for i in ids:
        storage.delete_receipt(i)
    for b in db.list_budgets():
        db.delete_budget(b["category"])


def test_ids_unique_and_iso_currency(data):
    assert len(set(data)) == 3
    assert storage.get_receipt(data[1])["currency"] == "INR"


def test_totals_never_mix_currencies(data):
    assert analytics.total_spend("2025-01-01", "2025-02-28", "INR")["total"] == 1000
    assert analytics.total_spend("2025-01-01", "2025-02-28", "USD")["total"] == 20
    assert analytics.total_spend("2025-01-01", "2025-02-28", "INR", "Dining")["total"] == 600


def test_category_and_mom(data):
    cats = {c["category"]: c["amount"] for c in analytics.category_breakdown("2025-01-01", "2025-02-28", "INR")}
    assert cats == {"Food": 300, "Personal Care": 100, "Dining": 600}
    mom = analytics.month_over_month("2025-02", "INR")
    assert mom["previous"] == 400 and mom["current"] == 600 and mom["change_pct"] == 50.0


def test_merchant_breakdown_by_category(data):
    rows = analytics.merchant_breakdown("2025-01-01", "2025-02-28", "INR", 5, "Personal Care")
    assert rows == [{"merchant": "Shop A", "amount": 100, "count": 1}]


def test_forecast_uses_real_day():
    f = analytics.forecast("INR", today=date(2025, 2, 14))
    assert f["day"] == 14 and f["days_in_month"] == 28


def test_budget_status(data):
    db.upsert_budgets([{"category": "Dining", "monthly_limit": 500}])
    st = analytics.budget_status("2025-02", "INR")
    assert st[0]["category"] == "Dining" and st[0]["status"] == "exceeded"


def test_insights_rules_fallback(data):
    out = insights.get_insights("2025-02", "INR")
    assert out["source"] == "rules" and out["summary"] and isinstance(out["tips"], list)


def test_insights_ai_cached(data, monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda *a, **k: True)
    calls = []

    def fake_chat(*a, **k):
        calls.append(1)
        return json.dumps({"summary": "You spent INR 600.", "tips": ["Cook at home more."]})

    monkeypatch.setattr(llm, "chat", fake_chat)
    first = insights.get_insights("2025-02", "INR")
    second = insights.get_insights("2025-02", "INR")
    assert first["source"] == "ai" and second == first and len(calls) == 1


def test_chat_tool_dispatch(data, monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda *a, **k: True)
    replies = iter([
        {"content": "", "tool_calls": [{"function": {"name": "get_total_spend",
                                                     "arguments": {"from": "2025-02-01", "to": "2025-02-28",
                                                                   "category": "Dining"}}}]},
        {"content": "You spent 600 on dining."},
    ])
    seen = []

    def fake_tools(model, messages, tools):
        seen.append(messages[-1])
        return next(replies)

    monkeypatch.setattr(llm, "chat_tools", fake_tools)
    out = insights.chat("How much on dining in Feb 2025?", currency="INR")
    assert out["source"] == "ai" and "600" in out["answer"]
    assert out["tool_calls"][0]["name"] == "get_total_spend"
    assert json.loads(seen[1]["content"])["total"] == 600


def test_tool_arg_validation():
    assert "error" in insights.run_tool("get_total_spend", {"from": "bad", "to": "2025-01-01"}, "INR")
    assert "error" in insights.run_tool("get_merchant_breakdown", {"from": "2025-01-01", "to": "2025-01-31",
                                                                   "limit": 500}, "INR")
    assert "error" in insights.run_tool("drop_tables", {}, "INR")
