"""
Integration Tests for FastAPI Endpoints (v3, auth disabled via conftest, Ollama offline -> Tesseract).
"""
import os
import unittest
from fastapi.testclient import TestClient
from api import app
class TestFastAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
    def test_health_endpoint(self):
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["version"], "3.0.0")
    def test_categories_endpoint(self):
        res = self.client.get("/api/v1/categories")
        self.assertEqual(res.status_code, 200)
        self.assertIn("categories", res.json())
        self.assertGreater(len(res.json()["categories"]), 5)
        sample_path = os.path.join(os.path.dirname(__file__), "..", "sample_receipts", "sample_grocery.png")
        with open(sample_path, "rb") as f:
            res = self.client.post("/api/v1/scan", files={"file": ("test.png", f, "image/png")})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["engine_used"], "tesseract")
        self.assertEqual(data["extraction"]["merchant_name"], "SUPERMART RETAIL")
        self.assertEqual(len(data["items"]), 6)
        self.assertAlmostEqual(data["extraction"]["total"], 1349.25)
        # Save through API
        ext = data["extraction"]
        save_payload = {
            "merchant_name": ext["merchant_name"],
            "date": ext["date"],
            "total": ext["total"],
            "currency": ext["currency"],
            "upload_id": data["upload_id"],
            "items": [{"item_name": i["item_name"], "amount": i["amount"], "category": i["category"],
                       "original_category": i["category"]} for i in data["items"]],
        }
        res_save = self.client.post("/api/v1/receipts", json=save_payload)
        self.assertEqual(res_save.status_code, 201)
        new_id = res_save.json()["id"]
        # Duplicate upload is flagged
        res_dup = self.client.post("/api/v1/receipts", json=save_payload)
        self.assertEqual(res_dup.status_code, 409)
        # Clean up
        res_del = self.client.delete(f"/api/v1/receipts/{new_id}")
        self.assertEqual(res_del.status_code, 200)
if __name__ == "__main__":
    unittest.main()
