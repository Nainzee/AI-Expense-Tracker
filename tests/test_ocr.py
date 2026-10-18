"""
Unit & Integration Tests for Receipt OCR & Spatial Parsing Pipeline.
"""

import os
import unittest
from PIL import Image
from ocr_pipeline import (
    clean_currency_token,
    detect_currency_symbol,
    is_valid_merchant_name,
    parse_line_items,
    extract_receipt_metadata,
    extract_receipt_items,
    ReceiptMetadata,
)


class TestOCRPipeline(unittest.TestCase):

    def test_clean_currency_token(self):
        self.assertEqual(clean_currency_token("340.00"), 340.0)
        self.assertEqual(clean_currency_token("Rs 1,250.50"), 1250.50)
        self.assertEqual(clean_currency_token("₹95"), 95.0)
        self.assertEqual(clean_currency_token("$5.99"), 5.99)
        self.assertEqual(clean_currency_token("-15.00", allow_negative=True), -15.00)
        self.assertIsNone(clean_currency_token("abc"))
        self.assertIsNone(clean_currency_token("."))

    def test_currency_detection(self):
        self.assertEqual(detect_currency_symbol("Total: $24.20 \n 0.778 kg @ $5.99/kg"), "$")
        self.assertEqual(detect_currency_symbol("Grand Total Rs 1349.25 \n CGST 5%"), "₹")
        self.assertEqual(detect_currency_symbol("Total €45.00"), "€")

    def test_merchant_validation(self):
        self.assertFalse(is_valid_merchant_name("HRESEESESSESSSESSESSSESESES SEES ESSE ESET ESE"))
        self.assertFalse(is_valid_merchant_name("=============="))
        self.assertTrue(is_valid_merchant_name("SUPERMART RETAIL"))
        self.assertTrue(is_valid_merchant_name("WOOLWORTHS SUPERMARKETS"))
        self.assertTrue(is_valid_merchant_name("The Artisan Cafe & Bistro"))

    def test_scale_line_attachment_and_negative_discounts(self):
        lines = [
            {"text": "LETTUCE ICEBERG"},
            {"text": "0.778 kg NET @ $5.99/kg $4.66"},
            {"text": "BREAD WHOLEMEAL $3.50"},
            {"text": "LOYALTY - $2.50"},
            {"text": "CASH 50.00"},
            {"text": "CHANGE 44.34"},
        ]
        items = parse_line_items(lines)
        self.assertEqual(len(items), 3, "LETTUCE, BREAD, LOYALTY should be kept; CASH and CHANGE skipped")
        # Check Lettuce
        self.assertEqual(items[0].item_name, "LETTUCE ICEBERG")
        self.assertEqual(items[0].amount, 4.66)
        self.assertEqual(items[0].quantity, 0.778)
        self.assertEqual(items[0].unit_price, 5.99)
        # Check Bread
        self.assertEqual(items[1].item_name, "BREAD WHOLEMEAL")
        self.assertEqual(items[1].amount, 3.50)
        # Check Loyalty
        self.assertEqual(items[2].item_name, "LOYALTY")
        self.assertEqual(items[2].amount, -2.50)

    def test_parse_line_items_units_protection(self):
        raw_lines = [
            {"text": "Basmati Rice 5kg 340.00"},
            {"text": "Sunflower Oil 1L 165.00"},
            {"text": "Amul Butter 500g 275.00"},
            {"text": "Tata Tea Gold 500g 290.00"},
        ]
        items = parse_line_items(raw_lines)
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0].amount, 340.0)
        self.assertEqual(items[1].amount, 165.0)
        self.assertEqual(items[2].amount, 275.0)
        self.assertEqual(items[3].amount, 290.0)

    def test_parse_line_items_header_suppression(self):
        raw_lines = [
            {"text": "ITEM DESCRIPTION PRICE"},
            {"text": "SL NO PARTICULARS QTY RATE AMOUNT"},
            {"text": "Tel: 080-25551234"},
            {"text": "Date: 18-Oct-2026 Time: 14:30"},
            {"text": "DATE 06/01/2016 14:22"},
            {"text": "SUBTOTAL 1285.00"},
            {"text": "CGST 5% 32.12"},
            {"text": "GRAND TOTAL Rs 1349.24"},
            {"text": "CASH 50.00"},
            {"text": "CHANGE 25.00"},
            {"text": "Thank you for shopping with us!"},
        ]
        items = parse_line_items(raw_lines)
        self.assertEqual(len(items), 0, "All header, contact, total, tender, and footer lines should be suppressed")

    def test_sample_grocery_receipt_extraction(self):
        sample_path = os.path.join(os.path.dirname(__file__), "..", "sample_receipts", "sample_grocery.png")
        img = Image.open(sample_path)
        items, raw, meta, _ = extract_receipt_items(img, use_trocr=False)
        self.assertEqual(meta["store_name"], "SUPERMART RETAIL")
        self.assertEqual(len(items), 6)
        self.assertEqual(meta["reconciliation_status"], "Verified Match")


if __name__ == "__main__":
    unittest.main()
