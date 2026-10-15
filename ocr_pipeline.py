
import re
import cv2
import numpy as np
from PIL import Image
import pytesseract

# Uncomment and edit this line if you are on Windows:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Lazy-load TrOCR so app starts faster
_trocr_processor = None
_trocr_model     = None

def load_trocr():
    """Load TrOCR model once and reuse (lazy loading saves startup time)."""
    global _trocr_processor, _trocr_model
    if _trocr_processor is None:
        print("Loading TrOCR model (first run, will download ~1GB)...")
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        _trocr_processor = TrOCRProcessor.from_pretrained("microsoft/trocr-base-printed")
        _trocr_model     = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-base-printed")
        print("TrOCR loaded.")
    return _trocr_processor, _trocr_model


def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Preprocess a receipt image for better OCR accuracy.
    Steps: convert to numpy → grayscale → denoise → sharpen → threshold → back to PIL
    """
    img = np.array(image.convert("RGB"))

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # Denoise (removes camera noise / JPEG artifacts)
    denoised = cv2.fastNlMeansDenoising(gray, h=10)

    # Sharpen the image (makes text edges crisper)
    kernel    = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(denoised, -1, kernel)

    # Adaptive thresholding (handles uneven lighting on real receipts)
    thresh = cv2.adaptiveThreshold(
        sharpened, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2
    )

    return Image.fromarray(thresh)


def extract_text_trocr(image: Image.Image) -> str:
    """
    Use TrOCR (deep learning) to extract text from image.
    TrOCR processes the image line by line for best results.
    """
    processor, model = load_trocr()
    processed = preprocess_image(image).convert("RGB")

    # TrOCR works best on individual lines, so we split the image into horizontal strips
    width, height = processed.size
    lines         = []
    strip_height  = max(30, height // 20)   # Each strip is 1/20th of image height

    for y in range(0, height, strip_height):
        strip = processed.crop((0, y, width, min(y + strip_height, height)))
        if strip.size[1] < 10:
            continue

        pixel_values = processor(images=strip, return_tensors="pt").pixel_values
        import torch
        with torch.no_grad():
            generated_ids = model.generate(pixel_values, max_new_tokens=64)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
        if text:
            lines.append(text)

    return "\n".join(lines)


def extract_text_tesseract(image: Image.Image) -> str:
    """
    Use Tesseract OCR as a fallback.
    Supports both English and Hindi (hin+eng).
    """
    processed = preprocess_image(image)

    # Try Hindi + English first, fall back to English only
    try:
        text = pytesseract.image_to_string(processed, lang="hin+eng", config="--psm 6")
    except Exception:
        text = pytesseract.image_to_string(processed, lang="eng", config="--psm 6")

    return text


def parse_receipt_lines(raw_text: str) -> list[dict]:
    """
    Parse raw OCR text into structured line items.
    Looks for lines that contain a price (number with optional ₹, Rs, decimal).
    Returns list of dicts: {item_name, amount}
    """
    lines  = raw_text.split("\n")
    items  = []
    seen   = set()

    # Pattern: optional ₹/Rs, then digits, optional decimal
    price_pattern = re.compile(
        r"(Rs\.?|₹|INR)?\s*(\d{1,5}(?:\.\d{1,2})?)", re.IGNORECASE
    )

    # Words that indicate non-item lines (header, footer, totals)
    skip_keywords = {
        "total", "subtotal", "sub total", "grand total", "tax", "gst",
        "cgst", "sgst", "igst", "discount", "net amount", "bill",
        "invoice", "receipt", "date", "time", "phone", "address",
        "thank you", "thanks", "visit", "cashier", "counter",
        "vat", "service charge", "round off", "change", "cash",
        "card", "upi", "paid", "balance", "savings"
    }

    for line in lines:
        line = line.strip()
        if len(line) < 3:
            continue

        # Skip lines that are clearly header/footer/total rows
        line_lower = line.lower()
        if any(kw in line_lower for kw in skip_keywords):
            continue

        # Check if line has a price
        match = price_pattern.search(line)
        if not match:
            continue

        amount_str = match.group(2)
        amount     = float(amount_str)

        # Skip amounts that are too small (likely not a product price)
        if amount < 1:
            continue

        # Extract item name: everything before the price match
        item_name = line[:match.start()].strip()
        item_name = re.sub(r"[^\w\s\-]", "", item_name).strip()  # Remove special chars
        item_name = re.sub(r"\s+", " ", item_name)               # Normalize spaces

        if len(item_name) < 2:
            continue

        # Deduplicate
        key = f"{item_name.lower()}_{amount}"
        if key in seen:
            continue
        seen.add(key)

        items.append({
            "item_name": item_name,
            "amount":    round(amount, 2)
        })

    return items


def extract_receipt_items(image: Image.Image, use_trocr: bool = True) -> tuple[list[dict], str]:
    """
    Main function: extract structured items from a receipt image.

    Returns:
        items     — list of {item_name, amount}
        raw_text  — the raw OCR output (useful for debugging / showing in UI)
    """
    try:
        if use_trocr:
            raw_text = extract_text_trocr(image)
        else:
            raw_text = extract_text_tesseract(image)
    except Exception as e:
        print(f"OCR error, falling back to Tesseract: {e}")
        raw_text = extract_text_tesseract(image)

    items = parse_receipt_lines(raw_text)
    return items, raw_text