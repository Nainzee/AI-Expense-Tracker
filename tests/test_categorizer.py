"""
Unit Tests for Hybrid Categorization Engine.
"""

import unittest
from categorizer import predict_category, categorize_items, CANONICAL_CATEGORIES


class TestCategorizer(unittest.TestCase):

    def test_canonical_categories_membership(self):
        self.assertIn("Food", CANONICAL_CATEGORIES)
        self.assertIn("Drinks", CANONICAL_CATEGORIES)
        self.assertIn("Medical", CANONICAL_CATEGORIES)
        self.assertIn("Household", CANONICAL_CATEGORIES)
        self.assertIn("Personal Care", CANONICAL_CATEGORIES)
        self.assertIn("Electronics", CANONICAL_CATEGORIES)
        self.assertIn("Apparel", CANONICAL_CATEGORIES)
        self.assertIn("Dining", CANONICAL_CATEGORIES)

    def test_high_precision_rules(self):
        cases = [
            ("Basmati Rice 5kg", "Food"),
            ("Coca Cola 2L", "Drinks"),
            ("Colgate Toothpaste", "Personal Care"),
            ("Dolo 650 Tablets", "Medical"),
            ("Surf Excel Detergent", "Household"),
            ("USB-C Charging Cable", "Electronics"),
            ("Cotton T-Shirt", "Apparel"),
            ("Margherita Pizza", "Dining"),
            ("Chocolate Brownie", "Bakery"),
        ]
        for name, expected_cat in cases:
            cat, conf = predict_category(name)
            self.assertEqual(cat, expected_cat, f"Failed for {name}: expected {expected_cat}, got {cat}")
            self.assertGreaterEqual(conf, 0.70)

    def test_categorize_items_batch(self):
        items = [
            {"item_name": "Amul Butter 500g", "amount": 275.0},
            {"item_name": "Tata Tea Gold", "amount": 290.0},
        ]
        res = categorize_items(items)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["category"], "Food")
        self.assertEqual(res[1]["category"], "Drinks")
        self.assertIn("confidence", res[0])

    def test_negative_filter_and_produce(self):
        self.assertEqual(predict_category("LETTUCE ICEBERG")[0], "Food")
        self.assertEqual(predict_category("CASH")[0], "Other")
        self.assertEqual(predict_category("SPECIAL")[0], "Other")
        self.assertEqual(predict_category("0.778 kg NET")[0], "Other")
        self.assertEqual(predict_category("LOYALTY DISCOUNT")[0], "Other")
        self.assertEqual(predict_category("CHANGE 25.80")[0], "Other")


if __name__ == "__main__":
    unittest.main()
