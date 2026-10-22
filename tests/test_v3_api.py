"""v3 endpoint tests: auth, budgets, analytics, insights, export, file access."""
import os

import pytest
from fastapi.testclient import TestClient

from api import app

client = TestClient(app)
SAMPLE = os.path.join(os.path.dirname(__file__), "..", "sample_receipts", "sample_dining.png")


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("APP_USERNAME", "alice")
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-that-is-long-enough-123")


def test_auth_required(auth_on):
    assert client.get("/api/v1/receipts").status_code == 401
    assert client.get("/api/v1/health").status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": "nope"}).status_code == 401
    tok = client.post("/api/v1/auth/login", json={"username": "alice", "password": "s3cret"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/receipts", headers=h).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).json()["username"] == "alice"
    assert client.get("/api/v1/receipts", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_login_unconfigured(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    for k in ("APP_USERNAME", "APP_PASSWORD", "APP_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert client.post("/api/v1/auth/login", json={"username": "a", "password": "b"}).status_code == 503


def test_signup_and_login(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "test.db"))
    # Test signup
    res = client.post("/api/v1/auth/signup", json={"username": "bob", "password": "password123"})
    assert res.status_code == 201
    assert res.json()["username"] == "bob"

    # Duplicate username fails
    res_dup = client.post("/api/v1/auth/signup", json={"username": "bob", "password": "password123"})
    assert res_dup.status_code == 400

    # Short password fails
    res_short = client.post("/api/v1/auth/signup", json={"username": "bobby", "password": "12"})
    assert res_short.status_code == 422

    # Login with wrong password fails
    res_fail = client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrongpassword"})
    assert res_fail.status_code == 401

    # Login with correct password succeeds
    res_ok = client.post("/api/v1/auth/login", json={"username": "bob", "password": "password123"})
    assert res_ok.status_code == 200
    assert "access_token" in res_ok.json()

    # Access protected route
    h = {"Authorization": f"Bearer {res_ok.json()['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["username"] == "bob"


def test_user_session_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "test.db"))

    # 1. User 1 (Alice) signs up
    res1 = client.post("/api/v1/auth/signup", json={"username": "alice", "password": "password123"})
    assert res1.status_code == 201
    tok1 = res1.json()["access_token"]
    h1 = {"Authorization": f"Bearer {tok1}"}

    # Alice initially has 0 receipts
    assert client.get("/api/v1/receipts", headers=h1).json() == []

    # 2. User 2 (Bob) signs up
    res2 = client.post("/api/v1/auth/signup", json={"username": "bob", "password": "password456"})
    assert res2.status_code == 201
    tok2 = res2.json()["access_token"]
    h2 = {"Authorization": f"Bearer {tok2}"}

    # Bob initially has 0 receipts
    assert client.get("/api/v1/receipts", headers=h2).json() == []

    # Alice creates a receipt
    body = {
        "merchant_name": "Alice Store",
        "date": "2026-10-22",
        "total": 500.0,
        "currency": "INR",
        "items": [{"item_name": "Book", "category": "Office & Stationery", "amount": 500.0, "quantity": 1}],
    }
    r = client.post("/api/v1/receipts", json=body, headers=h1)
    assert r.status_code == 201

    # Alice sees 1 receipt
    alice_receipts = client.get("/api/v1/receipts", headers=h1).json()
    assert len(alice_receipts) == 1
    assert alice_receipts[0]["store"] == "Alice Store"

    # Bob STILL sees 0 receipts (complete data isolation)
    bob_receipts = client.get("/api/v1/receipts", headers=h2).json()
    assert len(bob_receipts) == 0

    # Bob's dashboard shows 0 total spent
    bob_summary = client.get("/api/v1/analytics/summary", headers=h2).json()
    assert bob_summary["total_spent"] == 0.0


def test_full_flow():
    with open(SAMPLE, "rb") as f:
        scan = client.post("/api/v1/scan", files={"file": ("r.png", f, "image/png")}).json()
    ext = scan["extraction"]
    assert ext["merchant_name"] == "THE ARTISAN CAFE & BISTRO"
    first = scan["items"][0]
    body = {"merchant_name": ext["merchant_name"], "date": ext["date"], "total": ext["total"], "currency": "INR",
            "upload_id": scan["upload_id"],
            "items": [{"item_name": i["item_name"], "amount": i["amount"], "category": i["category"],
                       "original_category": i["category"]} for i in scan["items"]]}
    body["items"][0]["category"] = "Bakery" if first["category"] != "Bakery" else "Food"
    rid = client.post("/api/v1/receipts", json=body).json()["id"]
    try:
        import storage
        assert storage.get_user_correction(first["item_name"]) == body["items"][0]["category"]
        assert client.get(f"/api/v1/receipts/{rid}/file").status_code == 200
        lst = client.get("/api/v1/receipts", params={"q": "artisan"}).json()
        assert any(r["id"] == rid for r in lst)
        s = client.get("/api/v1/analytics/summary", params={"from": "2026-10-01", "to": "2026-10-31"}).json()
        assert s["total"] >= ext["total"] and s["per_category"]
        assert client.put("/api/v1/budgets", json=[{"category": "Dining", "monthly_limit": 1000}]).status_code == 200
        assert client.get("/api/v1/insights", params={"month": "2026-10"}).json()["source"] == "rules"
        csv_text = client.get("/api/v1/export/csv").text
        assert "THE ARTISAN CAFE" in csv_text
        upd = dict(body, notes="edited")
        assert client.put(f"/api/v1/receipts/{rid}", json=upd).json()["notes"] == "edited"
        chat = client.post("/api/v1/insights/chat", json={"message": "How much did I spend?"}).json()
        assert chat["source"] == "rules" and chat["answer"]
    finally:
        client.delete(f"/api/v1/receipts/{rid}")
        client.delete("/api/v1/budgets/Dining")


def test_bad_upload_rejected():
    r = client.post("/api/v1/scan", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400
