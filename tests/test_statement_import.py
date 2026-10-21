"""Statement import end to end: preview/commit, de-dup, receipt matching, analytics de-dup, recurring, API errors."""
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

import storage
from backend import analytics, config, db, insights
from backend.main import app
from backend.statements import recurring, service

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_statements")
client = TestClient(app)
API = "/api/v1"

RECEIPTS = {  # the four sample receipts (sample_generator.py)
    "SUPERMART RETAIL": ("2026-10-18", 1349.25, "Food"),
    "THE ARTISAN CAFE & BISTRO": ("2026-10-16", 1239.00, "Dining"),
    "APOLLO MEDPLUS PHARMACY": ("2026-10-17", 465.36, "Medical"),
    "DIGITAL TECH HUB": ("2026-10-15", 2591.28, "Electronics"),
}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "r.db"))
    service._CACHE.clear()
    yield


def _file(name):
    return open(os.path.join(SAMPLES, name), "rb").read()


def _preview(name, password=None, data=None):
    form = {"password": password} if password else {}
    return client.post(f"{API}/statements/preview", files={"file": (name, data or _file(name))}, data=form)


def _import(name, password=None):
    p = _preview(name, password)
    assert p.status_code == 200, p.text
    r = client.post(f"{API}/statements/commit", json={"token": p.json()["token"]})
    assert r.status_code == 200, r.text
    return r.json()


def _receipt(store, day=None, total=None, category=None):
    d, t, c = RECEIPTS.get(store, (day, total, category))
    body = {"merchant_name": store, "date": day or d, "total": total or t, "currency": "INR", "force": True,
            "items": [{"item_name": "Item", "amount": total or t, "category": category or c}]}
    r = client.post(f"{API}/receipts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _txns(**params):
    return client.get(f"{API}/transactions", params={"limit": 500, **params}).json()


def test_each_sample_parses():
    expected = {"hdfc_aug_oct_2026.csv": (29, "hdfc", "XX1234"), "hdfc_oct_2026.xlsx": (9, "hdfc", "XX1234"),
                "icici_aug_oct_2026.pdf": (11, "icici", "XX5678"), "phonepe_oct_2026.pdf": (5, "phonepe", None)}
    for name, (n, source, hint) in expected.items():
        p = _preview(name).json()
        assert (p["rows_parsed"], p["source"], p["account_hint"], p["needs_mapping"]) == (n, source, hint, False), name
        assert p["likely_new"] == n and p["already_imported"] is False


def test_protected_pdf_via_api():
    r = _preview("icici_aug_oct_2026_protected.pdf")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "password_required"
    r = _preview("icici_aug_oct_2026_protected.pdf", "nope")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "password_incorrect"
    assert _import("icici_aug_oct_2026_protected.pdf", "ICICI5678")["new"] == 11


def test_reimport_and_overlap_dedupe():
    first = _import("hdfc_aug_oct_2026.csv")
    assert first["new"] == 29 and first["duplicates_skipped"] == 0
    again = _import("hdfc_aug_oct_2026.csv")
    assert again["new"] == 0 and again["statement_id"] is None and again["duplicates_skipped"] == 29
    assert len(client.get(f"{API}/statements").json()) == 1
    assert _preview("hdfc_aug_oct_2026.csv").json()["already_imported"] is True
    xlsx = _import("hdfc_oct_2026.xlsx")  # 11..17 Oct overlap the CSV
    assert (xlsx["new"], xlsx["duplicates_skipped"]) == (2, 7)
    upi = _import("phonepe_oct_2026.pdf")  # two Swiggy + Apollo share UTRs with the bank rows
    assert (upi["new"], upi["duplicates_skipped"]) == (2, 3)
    metro = [t for t in _txns(q="metro")["items"]]
    assert len(metro) == 2  # same day, same amount, different balance -> both kept
    assert _txns()["total"] == 29 + 2 + 2


def test_delete_statement_removes_rows():
    s = _import("icici_aug_oct_2026.pdf")
    assert client.delete(f"{API}/statements/{s['statement_id']}").status_code == 200
    assert _txns()["total"] == 0
    assert client.delete(f"{API}/statements/{s['statement_id']}").status_code == 404


def test_manual_mapping_flow():
    data = b"01/10/2026,UPI-ZOMATO-zomato@hdfcbank-629000000001-Payment,250.00,\n02/10/2026,Refund x,,99.00\n"
    p = _preview("plain.csv", data=data).json()
    assert p["needs_mapping"] is True and p["rows_parsed"] == 0
    bad = client.post(f"{API}/statements/commit", json={"token": p["token"]})
    assert bad.status_code == 422
    mapping = {"header_row": -1, "date": 0, "description": 1, "debit": 2, "credit": 3}
    rp = client.post(f"{API}/statements/reparse", json={"token": p["token"], "mapping": mapping}).json()
    assert rp["needs_mapping"] is False and rp["rows_parsed"] == 2
    assert client.post(f"{API}/statements/reparse", json={"token": p["token"], "mapping": {**mapping, "credit": 2}}
                       ).status_code == 422
    assert client.post(f"{API}/statements/commit", json={"token": p["token"]}).json()["new"] == 2


def test_errors_token_size_auth(monkeypatch):
    assert client.post(f"{API}/statements/commit", json={"token": "a" * 64}).status_code == 410
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    assert _preview("hdfc_aug_oct_2026.csv").status_code == 413
    monkeypatch.setenv("AUTH_DISABLED", "0")
    assert client.get(f"{API}/transactions").status_code == 401
    assert client.post(f"{API}/statements/preview", files={"file": ("x.csv", b"a,b")}).status_code == 401


def test_receipt_first_then_import_autolinks_each_once():
    ids = {store: _receipt(store)["id"] for store in RECEIPTS}
    csv = _import("hdfc_aug_oct_2026.csv")
    assert csv["matched_receipts"] == 3  # Supermart's bank row is only in the XLSX
    xlsx = _import("hdfc_oct_2026.xlsx")
    assert xlsx["matched_receipts"] == 1
    linked = {t["matched_receipt_id"] for t in _txns(status="matched")["items"]}
    assert linked == set(ids.values())
    detail = client.get(f"{API}/receipts/{ids['APOLLO MEDPLUS PHARMACY']}").json()
    assert detail["bank_match"]["linked"]["amount"] == 465.36
    listed = {r["id"]: r["linked_transaction_id"] for r in client.get(f"{API}/receipts").json()}
    assert all(listed[i] for i in ids.values())


def test_import_first_then_receipt_links():
    _import("hdfc_aug_oct_2026.csv")
    r = _receipt("APOLLO MEDPLUS PHARMACY")
    assert r["linked_transaction"]["amount"] == 465.36 and r["match_suggestions"] == []


def test_ambiguous_match_goes_to_review():
    _import("hdfc_aug_oct_2026.csv")
    # Two receipts that both fit the 16-Oct 1239.00 bank row equally well -> no auto link, suggestions instead
    a = _receipt("Artisan Bistro", "2026-10-16", 1239.00, "Dining")
    b = _receipt("Artisan Bistro Cafe", "2026-10-16", 1239.00, "Dining")
    assert a["linked_transaction"] and b["linked_transaction"] is None  # first one links (it was unique then)
    client.delete(f"{API}/receipts/{a['id']}")  # FK sets the link to NULL
    c = _receipt("Artisan Bistro", "2026-10-15", 1239.00, "Dining")
    assert c["linked_transaction"] is None
    review = client.get(f"{API}/transactions/review").json()
    sugg = [s for s in review["match_suggestions"] if s["transaction"]["amount"] == 1239.0]
    assert {s["receipt"]["id"] for s in sugg} >= {c["id"]}
    tid = sugg[0]["transaction"]["id"]
    rid = sugg[0]["receipt"]["id"]
    assert client.post(f"{API}/transactions/{tid}/candidates/{rid}/reject").status_code == 200
    review = client.get(f"{API}/transactions/review").json()
    assert all(s["receipt"]["id"] != rid or s["transaction"]["id"] != tid for s in review["match_suggestions"])
    other = b["id"] if rid != b["id"] else c["id"]
    ok = client.post(f"{API}/transactions/{tid}/link", json={"receipt_id": other})
    assert ok.status_code == 200 and ok.json()["receipt"]["id"] == other
    assert client.post(f"{API}/transactions/{tid}/link", json={"receipt_id": rid}).status_code == 409


def test_linked_pair_counted_once_and_unlink_restores():
    _import("hdfc_aug_oct_2026.csv")
    before = analytics.summary("2026-10-01", "2026-10-31", "INR")
    rec = _receipt("APOLLO MEDPLUS PHARMACY")
    after = analytics.summary("2026-10-01", "2026-10-31", "INR")
    assert after["total"] == before["total"]  # the receipt replaced the bank row
    cats = {c["category"]: c["amount"] for c in after["per_category"]}
    assert cats["Medical"] == 465.36
    assert after["source_mix"]["linked_count"] == 1 and after["source_mix"]["receipts"]["count"] == 1
    tid = rec["linked_transaction"]["id"]
    assert client.delete(f"{API}/transactions/{tid}/link").status_code == 200
    restored = analytics.summary("2026-10-01", "2026-10-31", "INR")
    assert round(restored["total"] - after["total"], 2) == 465.36
    # editing the receipt doesn't silently re-link a pair the user separated
    body = {"merchant_name": "APOLLO MEDPLUS PHARMACY", "date": "2026-10-17", "total": 465.36,
            "items": [{"item_name": "Item", "amount": 465.36, "category": "Medical"}]}
    put = client.put(f"{API}/receipts/{rec['id']}", json=body).json()
    assert put["bank_match"]["linked"] is None


def test_dashboard_total_equals_receipts_plus_unlinked_spending():
    for store in RECEIPTS:
        _receipt(store)
    for name in ("hdfc_aug_oct_2026.csv", "hdfc_oct_2026.xlsx", "icici_aug_oct_2026.pdf", "phonepe_oct_2026.pdf"):
        _import(name)
    s = analytics.summary(None, None, "INR")
    with storage.get_connection() as conn:
        receipts = conn.execute("SELECT SUM(total) FROM receipts").fetchone()[0]
        txns = conn.execute(f"SELECT SUM(amount) FROM transactions t WHERE {db.counted_predicate()}").fetchone()[0]
        refunds = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE direction='credit' "
                               "AND txn_type='refund' AND excluded=0").fetchone()[0]
        income = conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_type IN ('income','refund',"
                              "'transfer_self','card_bill_payment')").fetchone()[0]
    # refunds reduce spending
    assert income > 0 and refunds > 0 and round(receipts + txns - refunds, 2) == s["total"]
    mix = s["source_mix"]
    assert round(mix["receipts"]["amount"] + mix["statements"]["amount"], 2) == s["total"]
    assert s["payment_count"] == s["receipt_count"] + s["transaction_count"] and s["source_mix"]["linked_count"] == 4
    # non-spending rows (salary, refunds, self transfers, card bills) never reach the total
    assert s["total"] < 85000


def test_patch_apply_to_payee_and_memory_on_next_import():
    _import("hdfc_aug_oct_2026.csv")
    rahul = [t for t in _txns(q="rahul")["items"]]
    assert len(rahul) == 2 and all(t["needs_review"] for t in rahul)
    r = client.patch(f"{API}/transactions/{rahul[0]['id']}",
                     json={"category": "Dining", "txn_type": "expense", "apply_to_payee": True})
    assert r.status_code == 200 and r.json()["category_source"] == "user"
    after = _txns(q="rahul")["items"]
    assert all(t["category"] == "Dining" and not t["needs_review"] for t in after)
    assert client.patch(f"{API}/transactions/{rahul[0]['id']}", json={"category": "Nope"}).status_code == 422
    assert client.patch(f"{API}/transactions/{rahul[0]['id']}", json={"bogus": 1}).status_code == 422
    data = (b"Date,Narration,Withdrawal Amt.,Deposit Amt.\n20/10/2026,UPI-RAHUL SHARMA-9876543210@ybl-SBIN0001234-"
            b"629299900001-chai,80.00,\n")
    p = _preview("new.csv", data=data).json()
    client.post(f"{API}/statements/commit", json={"token": p["token"]})
    new = [t for t in _txns(q="chai")["items"]][0]
    assert (new["category"], new["category_source"], new["txn_type"]) == ("Dining", "memory", "expense")
    ex = client.patch(f"{API}/transactions/{new['id']}", json={"excluded": True}).json()
    assert ex["counted"] is False


def test_recurring_detection():
    _import("hdfc_aug_oct_2026.csv")
    _import("icici_aug_oct_2026.pdf")
    rec = client.get(f"{API}/transactions/recurring").json()
    names = {r["merchant"] for r in rec}
    assert {"Netflix", "Spotify"} <= names and "Swiggy" not in names
    netflix = next(r for r in rec if r["merchant"] == "Netflix")
    assert netflix["cadence"] == "monthly" and netflix["count"] == 3 and netflix["next_expected"] == "2026-11-04"
    assert recurring.detect([{"payee_key": "x", "date": d, "amount": a} for d, a in
                             [("2026-01-01", 100), ("2026-02-01", 100), ("2026-03-01", 300)]]) == []


def test_insights_and_chat_tools_see_bank_payments():
    _import("hdfc_aug_oct_2026.csv")
    ins = insights.get_insights("2026-09", "INR")
    assert "bank/UPI payment" in ins["summary"]
    payees = insights.run_tool("get_payee_spend", {"from": "2026-08-01", "to": "2026-10-31", "query": "swiggy"}, "INR")
    assert payees and payees[0]["count"] == 4
    assert "error" in insights.run_tool("get_payee_spend", {"from": "bad", "to": "2026-10-31"}, "INR")
    assert any(r["merchant"] == "Netflix" for r in insights.run_tool("get_recurring_payments", {}, "INR"))


def test_migration_v3_to_v4_keeps_rows(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(path))
    rid = storage.save_receipt("Old Shop", [{"item_name": "x", "amount": 5, "category": "Food"}], "2026-01-02", 5.0)
    with sqlite3.connect(path) as conn:
        conn.executescript("DROP VIEW spend_docs; DROP VIEW spend_lines; DROP TABLE match_candidates; "
                           "DROP TABLE payee_rules; DROP TABLE transactions; DROP TABLE statements; "
                           "PRAGMA user_version = 3;")
    storage.init_db()
    with storage.get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
        assert conn.execute("SELECT COUNT(*) FROM spend_docs").fetchone()[0] == 1
    assert storage.get_receipt(rid)["total"] == 5.0


def test_delete_single_transaction_and_undo_import():
    sid = _import("hdfc_aug_oct_2026.csv")["statement_id"]
    items = _txns()["items"]
    first = items[0]["id"]
    assert client.delete(f"{API}/transactions/{first}").json() == {"deleted": True}
    assert client.delete(f"{API}/transactions/{first}").status_code == 404
    assert len(_txns()["items"]) == len(items) - 1
    # Undo the whole import: statement and all its transactions go away
    assert client.delete(f"{API}/statements/{sid}").status_code == 200
    assert _txns()["items"] == []


def test_positional_pdf_parser_ignores_balance_column():
    """Regression: a 2-line ICICI-style layout must take the WITHDRAWALS column, never the BALANCE column."""
    from backend.statements.pdf_text import read_pdf
    rows, _ = read_pdf(open(os.path.join(SAMPLES, "icici_aug_oct_2026.pdf"), "rb").read())
    debits = [float(r[2].replace(",", "")) for r in rows[1:] if r[2]]
    balances = [float(r[4].replace(",", "")) for r in rows[1:] if r[4]]
    assert debits and max(debits) < min(balances)
    assert not any(r[1].lower().startswith("opening balance") for r in rows[1:])
