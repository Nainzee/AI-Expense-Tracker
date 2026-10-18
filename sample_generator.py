"""
Synthetic Receipt Image Generator for Testing and Demonstration.

Generates realistic high-DPI receipt images with itemized lines, taxes,
store headers, and formatting across multiple retail categories:
- Grocery Supermarket
- Pharmacy & Health
- Cafe & Bistro Dining
- Tech & Electronics
"""

from __future__ import annotations

import os
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont


def get_font(size: int = 16, bold: bool = False) -> ImageFont.ImageFont:
    """Get system font or fallback."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def create_receipt_image(
    store_name: str,
    address_lines: List[str],
    date_str: str,
    invoice_no: str,
    items: List[Tuple[str, float]],
    tax_rate: float = 0.05,
    footer_text: str = "Thank you for your visit!",
    save_path: str = "sample_receipts/receipt.png"
) -> str:
    """
    Render a clean, high-resolution receipt image with PIL.
    """
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    font_title = get_font(22, bold=True)
    font_bold = get_font(16, bold=True)
    font_main = get_font(15)
    font_small = get_font(13)

    width = 540
    # Dynamic height calculation
    line_count = len(items)
    height = 420 + (line_count * 34) + 120

    img = Image.new("RGB", (width, height), color="#FCFCFB")
    draw = ImageDraw.Draw(img)

    # Header
    y = 30
    draw.text((width // 2 - len(store_name) * 6, y), store_name, fill="#111827", font=font_title)
    y += 34
    for addr in address_lines:
        draw.text((width // 2 - len(addr) * 4, y), addr, fill="#4B5563", font=font_small)
        y += 22

    y += 10
    draw.line([(30, y), (width - 30, y)], fill="#9CA3AF", width=1)
    y += 14

    # Metadata
    draw.text((30, y), f"Date: {date_str}", fill="#1F2937", font=font_main)
    draw.text((width - 180, y), f"Inv: {invoice_no}", fill="#1F2937", font=font_main)
    y += 28

    draw.line([(30, y), (width - 30, y)], fill="#9CA3AF", width=1)
    y += 14

    # Table Column Headers
    draw.text((30, y), "ITEM DESCRIPTION", fill="#111827", font=font_bold)
    draw.text((width - 110, y), "PRICE (₹)", fill="#111827", font=font_bold)
    y += 26
    draw.line([(30, y), (width - 30, y)], fill="#4B5563", width=2)
    y += 14

    # Line Items
    subtotal = 0.0
    for name, price in items:
        subtotal += price
        draw.text((30, y), name, fill="#1F2937", font=font_main)
        price_str = f"{price:.2f}"
        draw.text((width - 40 - len(price_str) * 9, y), price_str, fill="#1F2937", font=font_main)
        y += 32

    draw.line([(30, y), (width - 30, y)], fill="#9CA3AF", width=1)
    y += 16

    # Financial Totals
    tax_amt = round(subtotal * tax_rate, 2)
    grand_total = round(subtotal + tax_amt, 2)

    # Subtotal
    draw.text((30, y), "SUBTOTAL", fill="#374151", font=font_main)
    sub_str = f"{subtotal:.2f}"
    draw.text((width - 40 - len(sub_str) * 9, y), sub_str, fill="#374151", font=font_main)
    y += 26

    # Tax
    tax_label = f"TAX (GST {int(tax_rate*100)}%)"
    draw.text((30, y), tax_label, fill="#374151", font=font_main)
    tax_str = f"{tax_amt:.2f}"
    draw.text((width - 40 - len(tax_str) * 9, y), tax_str, fill="#374151", font=font_main)
    y += 28

    draw.line([(30, y), (width - 30, y)], fill="#111827", width=2)
    y += 16

    # Grand Total
    draw.text((30, y), "GRAND TOTAL", fill="#111827", font=font_bold)
    gt_str = f"₹{grand_total:.2f}"
    draw.text((width - 40 - len(gt_str) * 10, y), gt_str, fill="#111827", font=font_bold)
    y += 38

    draw.line([(30, y), (width - 30, y)], fill="#9CA3AF", width=1)
    y += 20

    # Footer
    draw.text((width // 2 - len(footer_text) * 4, y), footer_text, fill="#4B5563", font=font_small)
    y += 24
    draw.text((width // 2 - 75, y), "*** Customer Copy ***", fill="#9CA3AF", font=font_small)

    img.save(save_path, dpi=(300, 300))
    return save_path


def generate_all_samples():
    """Generate sample receipts across major industries."""
    samples = {
        "sample_receipts/sample_grocery.png": {
            "store_name": "SUPERMART RETAIL",
            "address_lines": ["123 Market St, Indiranagar", "Tel: 080-25551234"],
            "date_str": "18-Oct-2026",
            "invoice_no": "INV-84920",
            "items": [
                ("Basmati Rice 5kg", 340.00),
                ("Sunflower Oil 1L", 165.00),
                ("Amul Butter 500g", 275.00),
                ("Tata Tea Gold 500g", 290.00),
                ("Colgate Toothpaste", 95.00),
                ("Dettol Handwash", 120.00),
            ],
            "tax_rate": 0.05,
            "footer_text": "Thank you for shopping with us!"
        },
        "sample_receipts/sample_pharmacy.png": {
            "store_name": "APOLLO MEDPLUS PHARMACY",
            "address_lines": ["45 Health Avenue, Koramangala", "GSTIN: 29AAAAA0000A1Z5"],
            "date_str": "17-Oct-2026",
            "invoice_no": "MED-10492",
            "items": [
                ("Dolo 650 Tablets 15s", 32.50),
                ("Benadryl Cough Syrup", 115.00),
                ("Vitamin C Chewable", 145.00),
                ("Dettol Antiseptic 100ml", 78.00),
                ("Band-Aid Washproof 10s", 45.00),
            ],
            "tax_rate": 0.12,
            "footer_text": "Wishing you good health!"
        },
        "sample_receipts/sample_dining.png": {
            "store_name": "THE ARTISAN CAFE & BISTRO",
            "address_lines": ["Plot 78, 100ft Road, Bengaluru", "Order No: 42 (Dine-In)"],
            "date_str": "16-Oct-2026",
            "invoice_no": "TBL-092",
            "items": [
                ("Artisan Cappuccino x2", 360.00),
                ("Margherita Pizza 10-inch", 420.00),
                ("Garlic Herb Bread", 180.00),
                ("Warm Chocolate Brownie", 220.00),
            ],
            "tax_rate": 0.05,
            "footer_text": "Taste the crafted difference!"
        },
        "sample_receipts/sample_electronics.png": {
            "store_name": "DIGITAL TECH HUB",
            "address_lines": ["Nexus Mall, 2nd Floor", "Ph: 080-49201920"],
            "date_str": "15-Oct-2026",
            "invoice_no": "TECH-58291",
            "items": [
                ("Braided USB-C Cable 2M", 399.00),
                ("Wireless Silent Mouse", 699.00),
                ("Fast Charging Adapter 30W", 899.00),
                ("Microfiber Cleaning Kit", 199.00),
            ],
            "tax_rate": 0.18,
            "footer_text": "1 Year Manufacturer Warranty Applied"
        }
    }

    generated_paths = []
    for path, data in samples.items():
        p = create_receipt_image(
            store_name=data["store_name"],
            address_lines=data["address_lines"],
            date_str=data["date_str"],
            invoice_no=data["invoice_no"],
            items=data["items"],
            tax_rate=data["tax_rate"],
            footer_text=data["footer_text"],
            save_path=path
        )
        generated_paths.append(p)
        print(f"Generated sample receipt: {p}")

    return generated_paths


if __name__ == "__main__":
    generate_all_samples()
