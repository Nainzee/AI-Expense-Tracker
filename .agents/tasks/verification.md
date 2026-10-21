# OCR Parsing Fix — Verification (No-Op Conclusion)

## Summary

No code changes were made to `ocr_pipeline.py` or any other file. Investigation
confirmed there is **no parser bug**. The pipeline already produces correct output,
and the full OCR test suite passes. The task was closed as a no-op after the parent
session confirmed Option 1.

## Root cause of the false alarm

The original diagnosis was based on a faulty reproduction command that read
**nonexistent dictionary keys**:

- It read `it.get('name')` and `meta.get('merchant')`, which always return `None`
  because the pipeline never emits those keys.
- The pipeline's real output keys are **`item_name`** (from
  `ExtractedItem.to_dict()` → `asdict`) and **`store_name`** (from
  `ReceiptMetadata` → `asdict`).

Nothing in the repository reads `name` or `merchant` on the pipeline output. The real
consumers (`api.py` `LineItemModel.item_name` / `ReceiptMetadataModel.store_name`,
`eval/benchmark.py` `ext["item_name"]`, the categorizer, the DB, and
`tests/test_ocr.py`) all consistently use `item_name` and `store_name`.

The underlying issue that originally broke OCR output was the **environment**
(missing Tesseract binary + dead/broken virtualenvs), which was already repaired
before this step. Once the environment was fixed, the OCR engine produced clean text
and the parser produced correct items — there was never a defect in the parsing layer.

## Before / After

Before and after are **identical** because no fix was needed and no code was changed.

## Verification 1 — reproduction loop (real keys)

Command (cwd = repo root):

```
.venv/bin/python -c "
from PIL import Image
import glob, ocr_pipeline as op
for path in sorted(glob.glob('sample_receipts/*.png')):
    items, raw, meta, _ = op.extract_receipt_items(Image.open(path), use_trocr=False)
    print(path, '| store_name=', repr(meta.get('store_name')))
    for it in items:
        print('    item_name=', repr(it.get('item_name')), '| amount=', it.get('amount'))
"
```

Output:

```
sample_receipts/sample_dining.png | store_name= 'THE ARTISAN CAFE & BISTRO'
    item_name= 'Artisan Cappuccino x2' | amount= 360.0
    item_name= 'Margherita Pizza 10-inch' | amount= 420.0
    item_name= 'Garlic Herb Bread' | amount= 180.0
    item_name= 'Warm Chocolate Brownie' | amount= 220.0
sample_receipts/sample_electronics.png | store_name= 'DIGITAL TECH HUB'
    item_name= 'Braided USB-C Cable 2M' | amount= 399.0
    item_name= 'Wireless Silent Mouse' | amount= 699.0
    item_name= 'Fast Charging Adapter 30W' | amount= 899.0
    item_name= 'Microfiber Cleaning Kit' | amount= 199.0
sample_receipts/sample_grocery.png | store_name= 'SUPERMART RETAIL'
    item_name= 'Basmati Rice 5 kg' | amount= 340.0
    item_name= 'Sunflower Oil 1 L' | amount= 165.0
    item_name= 'Amul Butter 500 g' | amount= 275.0
    item_name= 'Tata Tea Gold 500 g' | amount= 290.0
    item_name= 'Colgate Toothpaste' | amount= 95.0
    item_name= 'Dettol Handwash' | amount= 120.0
sample_receipts/sample_pharmacy.png | store_name= 'APOLLO MEDPLUS PHARMACY'
    item_name= 'Dolo 650 Tablets 15s' | amount= 32.5
    item_name= 'Benadryl Cough Syrup' | amount= 115.0
    item_name= 'Vitamin C Chewable' | amount= 145.0
    item_name= 'Dettol Antiseptic 100m' | amount= 78.0
```

All four receipts have populated, sensible `item_name` values and a populated
`store_name`. Junk lines (SUBTOTAL, TAX, GRAND TOTAL, Thank-you, Date/Tel/address)
do not appear as items.

## Verification 2 — OCR test suite

Command: `.venv/bin/python -m pytest tests/test_ocr.py -v`

Result: **7 passed** (0.50s), including:

- `test_sample_grocery_receipt_extraction` — asserts `meta["store_name"] == "SUPERMART RETAIL"`, `len(items) == 6`, and `reconciliation_status == "Verified Match"`.
- `test_parse_line_items_units_protection`, `test_parse_line_items_header_suppression`,
  `test_scale_line_attachment_and_negative_discounts`, `test_merchant_validation`,
  `test_currency_detection`, `test_clean_currency_token`.

No pre-existing test was changed.

## Conclusion

The parsing layer is correct as-is. The keys are `item_name` / `store_name`, not
`name` / `merchant`. No change to `ocr_pipeline.py` was required or made.
