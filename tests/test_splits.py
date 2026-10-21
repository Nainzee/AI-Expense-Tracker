"""Bill split: money math, compute rules, API, receipt edits, balances and analytics share scaling."""
import random
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import storage
from api import app
from backend import analytics, db, insights, splits

client = TestClient(app)
DAY = "2031-03-15"  # isolated month so analytics assertions don't see other tests' receipts


def _name(prefix="P"):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _person(name=None):
    r = client.post("/api/v1/people", json={"name": name or _name()})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _receipt(total=100.0, items=None, day=DAY, currency="INR"):
    items = items or [{"item_name": "Thing", "amount": total, "category": "Food"}]
    body = {"merchant_name": _name("Shop"), "date": day, "total": total, "currency": currency, "items": items,
            "force": True}
    return client.post("/api/v1/receipts", json=body).json()["id"], body


def _split(rid, payload):
    return client.put(f"/api/v1/receipts/{rid}/split", json=payload)


# ─── Money math ─────────────────────────────────────────────────────────────
def test_allocate_remainder_and_property():
    assert splits.allocate(10000, [1, 1, 1]) == [3334, 3333, 3333]
    assert splits.allocate(-10000, [1, 1, 1]) == [-3334, -3333, -3333]
    rng = random.Random(7)
    for _ in range(300):
        total = rng.randint(-50000, 500000)
        ws = [rng.choice([0, 1, 2.5, 33.33, 7]) for _ in range(rng.randint(1, 6))]
        if sum(ws) <= 0:
            continue
        assert sum(splits.allocate(total, ws)) == total
    with pytest.raises(splits.SplitError):
        splits.allocate(100, [0, 0])


def test_currency_exponents():
    assert splits.minor_exp("JPY") == 0 and splits.to_minor(1001, "JPY") == 1001
    assert splits.minor_exp("KWD") == 3 and splits.to_minor(1.2345, "KWD") == 1235
    assert splits.to_minor(0.125, "INR") == 13 and splits.from_minor(3334, "INR") == 33.34


def _rec(total, items=None, cur="INR"):
    return {"total": total, "currency": cur, "items": items or []}


def test_compute_methods():
    A, B = 1, 2
    parts = [{"person_id": None}, {"person_id": A}, {"person_id": B}]
    eq = splits.compute(_rec(100), None, "equal", parts)
    assert eq["shares"] == {None: 3334, A: 3333, B: 3333} and eq["you_lent"] == 6666 and eq["you_owe"] == 0

    pct = splits.compute(_rec(100), A, "percent", [dict(p, value=v) for p, v in zip(parts, [33.33, 33.33, 33.34])])
    assert sum(pct["shares"].values()) == 10000 and pct["you_owe"] == pct["your_share"] == 3333
    with pytest.raises(splits.SplitError, match="100"):
        splits.compute(_rec(100), None, "percent", [dict(p, value=30) for p in parts])

    with pytest.raises(splits.SplitError, match="10.00 INR left"):
        splits.compute(_rec(100), None, "exact", [dict(p, value=30) for p in parts])
    ex = splits.compute(_rec(100), None, "exact", [dict(p, value=v) for p, v in zip(parts, [50, 25, 25])])
    assert ex["shares"] == {None: 5000, A: 2500, B: 2500}

    sh = splits.compute(_rec(100), None, "shares", [dict(p, value=v) for p, v in zip(parts, [2, 1, 1])])
    assert sh["shares"] == {None: 5000, A: 2500, B: 2500}
    with pytest.raises(splits.SplitError):
        splits.compute(_rec(100), None, "shares", [dict(p, value=0) for p in parts])


def test_compute_by_item_distributes_tax():
    items = [{"item_name": "Pizza", "amount": 100}, {"item_name": "Salad", "amount": 50}]
    parts = [{"person_id": None}, {"person_id": 1}, {"person_id": 2}]
    res = splits.compute(_rec(165, items), None, "items", parts,
                         [{"item_index": 0, "person_ids": [None, 1]}, {"item_index": 1, "person_ids": [2]}])
    assert res["shares"] == {None: 5500, 1: 5500, 2: 5500}
    with pytest.raises(splits.SplitError):  # item 1 unassigned
        splits.compute(_rec(165, items), None, "items", parts, [{"item_index": 0, "person_ids": [None]}])


def test_compute_party_rules():
    with pytest.raises(splits.SplitError, match="payer"):
        splits.compute(_rec(100), 9, "equal", [{"person_id": None}, {"person_id": 1}])
    with pytest.raises(splits.SplitError, match="You must"):
        splits.compute(_rec(100), 1, "equal", [{"person_id": 1}, {"person_id": 2}])
    with pytest.raises(splits.SplitError, match="two"):
        splits.compute(_rec(100), None, "equal", [{"person_id": None}])
    with pytest.raises(splits.SplitError, match="greater than zero"):
        splits.compute(_rec(0), None, "equal", [{"person_id": None}, {"person_id": 1}])
    # You paid but aren't sharing the cost: you lent everything
    res = splits.compute(_rec(100), None, "equal", [{"person_id": 1}, {"person_id": 2}])
    assert res["your_share"] == 0 and res["you_lent"] == 10000


# ─── Migration ──────────────────────────────────────────────────────────────
def test_migration_v2_to_v3_keeps_rows(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(path))
    storage.init_db()
    rid = storage.save_receipt("Old Shop", [{"item_name": "x", "amount": 5, "category": "Food"}], "2026-01-02", 5.0)
    with sqlite3.connect(path) as conn:  # roll back to a v2-shaped DB
        conn.executescript("DROP VIEW spend_docs; DROP VIEW spend_lines; DROP TABLE match_candidates; "
                           "DROP TABLE payee_rules; DROP TABLE transactions; DROP TABLE statements; "
                           "DROP TABLE settlements; DROP TABLE split_items; DROP TABLE split_shares; "
                           "DROP TABLE splits; DROP TABLE people; ALTER TABLE receipts DROP COLUMN my_share; "
                           "ALTER TABLE line_items DROP COLUMN share_weight; PRAGMA user_version = 2;")
    storage.init_db()
    with storage.get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"people", "splits", "split_shares", "split_items", "settlements"} <= tables
        assert "my_share" in db._columns(conn, "receipts") and "share_weight" in db._columns(conn, "line_items")
    rec = storage.get_receipt(rid)
    assert rec["total"] == 5.0 and rec["my_share"] is None and rec["items"][0]["share_weight"] == 1.0


# ─── API ────────────────────────────────────────────────────────────────────
def test_people_idempotent_and_reserved():
    n = _name("Asha")
    r1 = client.post("/api/v1/people", json={"name": n})
    r2 = client.post("/api/v1/people", json={"name": n.upper()})
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert client.post("/api/v1/people", json={"name": "You"}).status_code == 422
    assert client.post("/api/v1/people", json={"name": ""}).status_code == 422
    assert any(p["id"] == r1.json()["id"] for p in client.get("/api/v1/people").json())


def test_split_lent_vs_owed_and_validation():
    a, b = _person(), _person()
    rid, _ = _receipt(100)
    try:
        r = _split(rid, {"participants": [{"person_id": None}, {"person_id": a}, {"person_id": b}]})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["you_lent"] == 66.66 and s["you_owe"] == 0 and s["payer"]["name"] == "You"
        assert s["shares"][0] == {"person_id": None, "name": "You", "amount": 33.34, "value": None}

        s = _split(rid, {"payer_id": a, "participants": [{"person_id": None}, {"person_id": a}]}).json()
        assert s["you_owe"] == 50.0 and s["you_lent"] == 0 and s["payer"]["id"] == a
        assert client.get(f"/api/v1/receipts/{rid}").json()["split"]["you_owe"] == 50.0
        row = next(x for x in client.get("/api/v1/receipts").json() if x["id"] == rid)
        assert row["split"]["you_owe"] == 50.0 and row["split"]["payer"]

        bad = [
            {"method": "exact", "participants": [{"person_id": None, "value": 10}, {"person_id": a, "value": 10}]},
            {"payer_id": b, "participants": [{"person_id": None}, {"person_id": a}]},
            {"payer_id": a, "participants": [{"person_id": a}, {"person_id": b}]},
            {"participants": [{"person_id": None}, {"person_id": 999999}]},
            {"participants": [{"person_id": None}, {"person_id": None}]},
        ]
        for p in bad:
            assert _split(rid, p).status_code == 422, p
        zero, _ = _receipt(0, [{"item_name": "free", "amount": 0, "category": "Food"}])
        assert _split(zero, {"participants": [{"person_id": None}, {"person_id": a}]}).status_code == 422
        client.delete(f"/api/v1/receipts/{zero}")
        assert client.put("/api/v1/receipts/nope/split", json={"participants": [{"person_id": None}]}).status_code == 404
    finally:
        client.delete(f"/api/v1/receipts/{rid}")


def test_receipt_edit_recomputes_or_removes_split():
    a = _person()
    rid, body = _receipt(100)
    try:
        _split(rid, {"participants": [{"person_id": None}, {"person_id": a}]})
        same = client.put(f"/api/v1/receipts/{rid}", json=dict(body, notes="x")).json()
        assert same["split"]["your_share"] == 50.0 and same["split_removed"] is False
        upd = client.put(f"/api/v1/receipts/{rid}", json=dict(body, total=80.0)).json()
        assert upd["split"]["your_share"] == 40.0 and upd["split_removed"] is False

        _split(rid, {"method": "exact", "participants": [{"person_id": None, "value": 30},
                                                         {"person_id": a, "value": 50}]})
        upd = client.put(f"/api/v1/receipts/{rid}", json=dict(body, total=90.0)).json()
        assert upd["split"] is None and upd["split_removed"] is True
        assert storage.get_receipt(rid)["my_share"] is None
        assert client.get(f"/api/v1/receipts/{rid}/split").status_code == 404
    finally:
        client.delete(f"/api/v1/receipts/{rid}")


def test_delete_receipt_cascades_split():
    a = _person()
    rid, _ = _receipt(100)
    _split(rid, {"participants": [{"person_id": None}, {"person_id": a}]})
    assert client.delete(f"/api/v1/receipts/{rid}").status_code == 200
    with storage.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM splits WHERE receipt_id = ?", (rid,)).fetchone()[0] == 0
    assert not any(s["receipt_id"] == rid for s in client.get("/api/v1/splits").json())


def test_balances_and_settle_up():
    a, b = _person(), _person()
    r1, _ = _receipt(100)
    r2, _ = _receipt(60)
    r3, _ = _receipt(10, currency="USD")
    try:
        _split(r1, {"participants": [{"person_id": None}, {"person_id": a}]})  # a owes you 50
        _split(r2, {"payer_id": a, "participants": [{"person_id": None}, {"person_id": a}, {"person_id": b}]})  # you owe a 20
        _split(r3, {"participants": [{"person_id": None}, {"person_id": a}]})  # a owes you 5 USD (never mixed)
        bal = client.get("/api/v1/splits/balances").json()
        mine = {(p["person_id"], p["currency"]): p["net"] for p in bal["people"]}
        assert mine[(a, "INR")] == 30.0 and mine[(a, "USD")] == 5.0 and (b, "INR") not in mine
        assert {t["currency"] for t in bal["totals"]} >= {"INR", "USD"}

        r = client.post("/api/v1/settlements", json={"person_id": a, "direction": "they_paid", "amount": 30,
                                                     "currency": "INR"})
        assert r.status_code == 201 and r.json()["amount"] == 30.0
        mine = {(p["person_id"], p["currency"]) for p in client.get("/api/v1/splits/balances").json()["people"]}
        assert (a, "INR") not in mine and (a, "USD") in mine
        assert client.post("/api/v1/settlements", json={"person_id": a, "direction": "they_paid", "amount": 0,
                                                        "currency": "INR"}).status_code == 422
        assert client.post("/api/v1/settlements", json={"person_id": 999999, "direction": "you_paid",
                                                        "amount": 1, "currency": "INR"}).status_code == 404
        assert insights.run_tool("get_split_balances", {}, "INR").keys() == {"totals", "people"}
    finally:
        for rid in (r1, r2, r3):
            client.delete(f"/api/v1/receipts/{rid}")


def test_analytics_counts_only_your_share():
    a, b = _person(), _person()
    frm, to = "2031-03-01", "2031-03-31"
    items = [{"item_name": "Pizza", "amount": 100, "category": "Dining"},
             {"item_name": "Salad", "amount": 50, "category": "Food"}]
    r1, _ = _receipt(300, [{"item_name": "Dinner", "amount": 300, "category": "Dining"}])
    r2, _ = _receipt(165, items)
    r3, _ = _receipt(40, [{"item_name": "Solo", "amount": 40, "category": "Food"}])
    try:
        _split(r1, {"participants": [{"person_id": None}, {"person_id": a}, {"person_id": b}]})
        _split(r2, {"method": "items", "participants": [{"person_id": None}, {"person_id": a}, {"person_id": b}],
                    "items": [{"item_index": 0, "person_ids": [None, a]}, {"item_index": 1, "person_ids": [b]}]})
        # 100 (equal third) + 55 (half pizza + proportional tax) + 40 (not split)
        assert analytics.total_spend(frm, to, "INR")["total"] == 195.0
        cats = {c["category"]: c["amount"] for c in analytics.category_breakdown(frm, to, "INR")}
        assert cats == {"Dining": 155.0, "Food": 40.0}
        assert analytics.summary(frm, to, "INR")["split_count"] == 2
        assert all(t["item_name"] != "Salad" for t in analytics.top_items(frm, to, "INR"))
        assert client.delete(f"/api/v1/receipts/{r1}/split").status_code == 200
        assert analytics.total_spend(frm, to, "INR")["total"] == 395.0
    finally:
        for rid in (r1, r2, r3):
            client.delete(f"/api/v1/receipts/{rid}")


def test_splits_require_auth(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("APP_USERNAME", "alice")
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-that-is-long-enough-123")
    for path in ("/api/v1/splits/balances", "/api/v1/people", "/api/v1/splits"):
        assert client.get(path).status_code == 401
