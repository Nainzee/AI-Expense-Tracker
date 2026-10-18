"""
Integration Tests for FastAPI Endpoints.
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
        self.assertEqual(data["version"], "2.0.0")

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
        self.assertTrue(data["success"])
        self.assertEqual(data["metadata"]["store_name"], "SUPERMART RETAIL")
        self.assertEqual(len(data["items"]), 6)

        # Save through API
        save_payload = {
            "store_name": data["metadata"]["store_name"],
            "items": data["items"],
            "scan_date": data["metadata"]["date"],
            "grand_total": data["total_amount"],
        }
        res_save = self.client.post("/api/v1/receipts", json=save_payload)
        self.assertEqual(res_save.status_code, 201)
        self.assertTrue(res_save.json()["success"])
        new_id = res_save.json()["receipt_id"]

        # Clean up
        res_del = self.client.delete(f"/api/v1/receipts/{new_id}")
        self.assertEqual(res_del.status_code, 200)


if __name__ == "__main__":
    unittest.main()
