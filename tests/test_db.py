"""
Unit Tests for Receipts Database & Analytics Engine.
"""

import unittest
from monthly_summary import (
    save_receipt,
    get_receipt,
    delete_receipt,
    list_receipts,
    get_monthly_data,
    generate_insights,
)


class TestDatabaseAndAnalytics(unittest.TestCase):

    def test_save_get_delete_lifecycle(self):
        items = [
            {"item_name": "Organic Honey", "amount": 250.0, "category": "Food"},
            {"item_name": "Green Tea", "amount": 180.0, "category": "Drinks"},
        ]
        rid = save_receipt(
            store_name="Test Health Mart",
            items=items,
            scan_date="2026-10-18",
            grand_total=430.0,
            notes="Integration test"
        )
        self.assertTrue(rid.startswith("REC-202610-"))

        # Retrieve
        rec = get_receipt(rid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["store"], "Test Health Mart")
        self.assertEqual(len(rec["items"]), 2)
        self.assertEqual(rec["total"], 430.0)

        # Delete
        success = delete_receipt(rid)
        self.assertTrue(success)
        self.assertIsNone(get_receipt(rid))

    def test_analytics_and_insights(self):
        data = get_monthly_data("2026-10")
        if data:
            self.assertIn("grand_total", data)
            self.assertIn("category_totals", data)
            insights = generate_insights(data)
            self.assertIsInstance(insights, list)
            self.assertGreater(len(insights), 0)


if __name__ == "__main__":
    unittest.main()
