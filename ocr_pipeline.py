"""
Advanced Receipt OCR & Information Extraction Pipeline.

Features:
- Dynamic resolution scaling, CLAHE contrast enhancement, auto-deskewing.
- Spatial line clustering via word-level bounding boxes (Tesseract image_to_data).
- Deep learning transcription with Microsoft TrOCR using contour-based line segmentation.
- Multi-currency symbol detection ($, €, £, ₹, ¥).
- Strict merchant name validator (filters out OCR noise like 'HRESEESESSESS...').
- Weight/Scale line detection (attaches '0.778 kg NET @ $5.99/kg $4.66' to previous item).
- Negative discount line preservation ('LOYALTY -15.00' -> -$15.00).
- Payment tender line rejection ('CASH 50', 'CHANGE 25.80' extracted as payment, not items).
- Date line suppression (prevents 'DATE 2016' becoming a 2016.00 item).
- Financial cross-reconciliation (compares line items sum against printed total).
- Visual bounding box annotator for human-in-the-loop inspection.
"""

from __future__ import annotations

import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytesseract

# ─── Configure Tesseract Binary Path ─────────────────────────────────────────
if not shutil.which("tesseract"):
    for candidate in [
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    ]:
        if os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            break

# ─── Lazy-Loaded TrOCR Engine ────────────────────────────────────────────────
_trocr_processor = None
_trocr_model = None


def load_trocr():
    """Lazy load Microsoft TrOCR model and processor."""
    global _trocr_processor, _trocr_model
    if _trocr_processor is None:
        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _trocr_processor = TrOCRProcessor.from_pretrained("microsoft/trocr-base-printed")
            _trocr_model = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-base-printed").to(device)
            _trocr_model.eval()
        except Exception as exc:
            print(f"[Warning] Failed to load TrOCR: {exc}. Falling back to Tesseract.")
            return None, None
    return _trocr_processor, _trocr_model


# ─── Data Structures ─────────────────────────────────────────────────────────
@dataclass
class BoundingBox:
    left: int
    top: int
    width: int
    height: int


@dataclass
class ExtractedItem:
    item_name: str
    amount: float
    quantity: float = 1.0
    unit_price: Optional[float] = None
    category: str = "Other"
    confidence: float = 0.85
    bbox: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReceiptMetadata:
    store_name: str = ""
    date: str = ""
    invoice_number: str = ""
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    grand_total: Optional[float] = None
    currency: str = "₹"
    payment_method: str = ""
    tendered: Optional[float] = None
    change: Optional[float] = None
    reconciliation_status: str = "Unverified"
    reconciliation_diff: float = 0.0


@dataclass
class ParsedReceipt:
    metadata: ReceiptMetadata
    items: List[ExtractedItem]
    raw_text: str
    annotated_image: Optional[Image.Image] = None

    def to_dict(self) -> dict:
        return {
            "metadata": asdict(self.metadata),
            "items": [item.to_dict() for item in self.items],
            "raw_text": self.raw_text,
        }


# ─── Image Preprocessing & Computer Vision ──────────────────────────────────
def locate_and_crop_receipt(cv_img: np.ndarray) -> np.ndarray:
    """
    Detect document boundaries against contrasting surfaces (e.g. wooden desk, dark table).
    Crops away non-receipt background to maximize effective text resolution and prevent border artifacts.
    """
    h, w = cv_img.shape[:2]
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    border_pixels = np.concatenate([thresh[0, :], thresh[-1, :], thresh[:, 0], thresh[:, -1]])
    dark_border_ratio = np.mean(border_pixels == 0)

    # Only crop if there is a distinct dark background surrounding the receipt
    if dark_border_ratio > 0.40:
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)
            if area > (h * w * 0.15) and area < (h * w * 0.95):
                rx, ry, rw, rh = cv2.boundingRect(largest)
                pad_x = int(rw * 0.03)
                pad_y = int(rh * 0.02)
                rx0 = max(0, rx - pad_x)
                ry0 = max(0, ry - pad_y)
                rx1 = min(w, rx + rw + pad_x)
                ry1 = min(h, ry + rh + pad_y)
                if (rx1 - rx0) > 100 and (ry1 - ry0) > 100:
                    return cv_img[ry0:ry1, rx0:rx1]
    return cv_img


def deskew_image(cv_img: np.ndarray) -> np.ndarray:
    """Detect and correct document skew using contour moments."""
    try:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 100:
            return cv_img

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        elif angle > 45:
            angle = 90 - angle
        else:
            angle = -angle

        # Only correct moderate skew angles (|angle| between 0.5 and 25 degrees)
        if 0.5 < abs(angle) < 25.0:
            h, w = cv_img.shape[:2]
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(
                cv_img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
            return rotated
    except Exception:
        pass
    return cv_img


def preprocess_receipt_image(image: Image.Image) -> Tuple[np.ndarray, np.ndarray]:
    """
    Apply multi-stage computer vision enhancement:
    1. Automatic document localization (crops away background surface/table).
    2. Dynamic upscaling for low-resolution inputs (< 1600px height).
    3. Auto-deskewing.
    4. Illumination flattening (background division to cancel phone shadows & paper wrinkles).
    5. CLAHE contrast boost & bilateral edge-preserving denoising.
    Returns: (color_cv_image, enhanced_grayscale_image)
    """
    img_rgb = np.array(image.convert("RGB"))
    cv_color = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)

    # 1. Localize and crop receipt from background table/desk
    cv_color = locate_and_crop_receipt(cv_color)

    # 2. Dynamic upscaling for optimal OCR DPI (aim for >= 1600px height)
    h, w = cv_color.shape[:2]
    if h < 1600:
        scale = min(2.5, 1700.0 / max(h, 1))
        cv_color = cv2.resize(cv_color, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # 3. Deskew
    cv_color = deskew_image(cv_color)

    # 4. Illumination flattening & CLAHE contrast boost
    gray = cv2.cvtColor(cv_color, cv2.COLOR_BGR2GRAY)
    bg = cv2.GaussianBlur(gray, (45, 45), 0)
    norm = cv2.divide(gray, bg, scale=255)

    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    enhanced = clahe.apply(norm)

    # 5. Edge-preserving denoising
    denoised = cv2.bilateralFilter(enhanced, d=7, sigmaColor=50, sigmaSpace=50)

    return cv_color, denoised


# ─── Spatial Line Clustering (Tesseract Engine) ──────────────────────────────
def extract_spatial_lines(enhanced_gray: np.ndarray) -> List[dict]:
    """
    Run Tesseract OCR and group words into spatially aligned lines.
    Preserves bounding boxes for words and line items.
    """
    data = pytesseract.image_to_data(
        enhanced_gray,
        config="--psm 6",
        output_type=pytesseract.Output.DATAFRAME
    )
    data = data[data.text.notnull()]
    data["clean_text"] = data.text.astype(str).str.strip()
    data = data[data.clean_text != ""]

    if data.empty:
        return []

    lines = []
    # Group by Tesseract line hierarchy
    for (b_num, p_num, l_num), group in data.groupby(["block_num", "par_num", "line_num"]):
        words = group.sort_values("left")
        line_text = " ".join(words.clean_text.tolist())
        min_left = int(words.left.min())
        min_top = int(words.top.min())
        max_right = int((words.left + words.width).max())
        max_bottom = int((words.top + words.height).max())
        mean_conf = float(words.conf[words.conf > 0].mean()) if (words.conf > 0).any() else 50.0

        lines.append({
            "text": line_text,
            "bbox": {
                "left": min_left,
                "top": min_top,
                "width": max_right - min_left,
                "height": max_bottom - min_top,
            },
            "confidence": mean_conf,
            "words": [
                {
                    "text": row["clean_text"],
                    "left": int(row["left"]),
                    "top": int(row["top"]),
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                }
                for _, row in words.iterrows()
            ]
        })

    lines.sort(key=lambda item: item["bbox"]["top"])
    return lines


# ─── Deep Learning Transcription (TrOCR Engine) ─────────────────────────────
def extract_lines_trocr(cv_color: np.ndarray) -> List[dict]:
    """
    Extract line segments using morphological contours and transcribe via Microsoft TrOCR.
    """
    processor, model = load_trocr()
    if processor is None or model is None:
        gray = cv2.cvtColor(cv_color, cv2.COLOR_BGR2GRAY)
        return extract_spatial_lines(gray)

    import torch
    device = next(model.parameters()).device

    gray = cv2.cvtColor(cv_color, cv2.COLOR_BGR2GRAY)
    bin_inv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 11
    )

    h, w = gray.shape
    kernel_w = max(18, w // 25)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 2))
    dilated = cv2.dilate(bin_inv, kernel, iterations=1)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    line_boxes = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw > 30 and 10 <= bh <= 90:
            line_boxes.append((x, y, bw, bh))

    line_boxes.sort(key=lambda b: b[1])

    lines = []
    for x, y, bw, bh in line_boxes:
        pad_y = max(2, int(bh * 0.15))
        pad_x = max(4, int(bw * 0.05))
        y0 = max(0, y - pad_y)
        y1 = min(h, y + bh + pad_y)
        x0 = max(0, x - pad_x)
        x1 = min(w, x + bw + pad_x)

        crop = cv_color[y0:y1, x0:x1]
        if crop.size == 0:
            continue

        pil_crop = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        pixel_values = processor(images=pil_crop, return_tensors="pt").pixel_values.to(device)

        with torch.no_grad():
            generated_ids = model.generate(pixel_values, max_new_tokens=48)
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

        if text and len(text) >= 2:
            lines.append({
                "text": text,
                "bbox": {"left": x, "top": y, "width": bw, "height": bh},
                "confidence": 88.0,
                "words": []
            })

    return lines


# ─── Currency & Merchant Validation ──────────────────────────────────────────
def detect_currency_symbol(raw_text: str) -> str:
    """
    Detect dominant currency symbol in receipt ($ , €, £, ₹, ¥).
    """
    dollar_count = len(re.findall(r"(\$|USD|AUD|CAD)", raw_text))
    rupee_count = len(re.findall(r"(₹|Rs\.?|INR)", raw_text, re.IGNORECASE))
    euro_count = len(re.findall(r"(€|EUR)", raw_text, re.IGNORECASE))
    pound_count = len(re.findall(r"(£|GBP)", raw_text, re.IGNORECASE))
    yen_count = len(re.findall(r"(¥|JPY)", raw_text, re.IGNORECASE))

    scores = {
        "$": dollar_count,
        "₹": rupee_count,
        "€": euro_count,
        "£": pound_count,
        "¥": yen_count,
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "₹"


def is_valid_merchant_name(name: str) -> bool:
    """
    Validate merchant name string, rejecting repetitive character noise (e.g. 'HRESEESESSESS...').
    """
    name = name.strip()
    if len(name) < 3 or len(name) > 60:
        return False

    cleaned = re.sub(r"[^a-zA-Z]", "", name.lower())
    if len(cleaned) < 3:
        return False

    counts = Counter(cleaned)
    most_common_char, count = counts.most_common(1)[0]
    # If a single character makes up >35% of the string
    if (count / len(cleaned)) > 0.35:
        return False

    # Low unique character ratio
    if len(counts) <= 3 and len(cleaned) > 8:
        return False

    # Repeated n-gram check (e.g. 'SES', 'ESS', 'ESE')
    for n in (2, 3):
        ngrams = [cleaned[i:i + n] for i in range(len(cleaned) - n + 1)]
        if ngrams:
            _, ng_count = Counter(ngrams).most_common(1)[0]
            if (ng_count * n / len(cleaned)) > 0.55:
                return False

    # Max consecutive identical characters e.g. 'SSSS'
    if re.search(r"(.)\1{3,}", name):
        return False

    # Must contain both vowel and consonant
    has_vowel = bool(re.search(r"[aeiouy]", cleaned))
    has_consonant = bool(re.search(r"[bcdfghjklmnpqrstvwxz]", cleaned))
    if not (has_vowel and has_consonant):
        return False

    return True


def clean_currency_token(token: str, allow_negative: bool = False) -> Optional[float]:
    """Clean and parse a string into float, handling thousands commas and optional negative sign."""
    token = token.strip()
    is_negative = token.startswith(("-", "–", "—")) or token.endswith(("-", "–", "—"))

    if "," in token and "." in token:
        token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")

    token = re.sub(r"[^\d.]", "", token)
    if not token or token == ".":
        return None
    try:
        val = float(token)
        if is_negative and allow_negative:
            val = -val
        return val if -500000.0 <= val <= 500000.0 else None
    except ValueError:
        return None


# ─── Semantic Information Extraction & Metadata ──────────────────────────────
CONTACT_KEYWORDS = {
    "tel", "phone", "ph", "mob", "mobile", "fax", "email", "www", "http",
    "gstin", "cin", "fssai", "tin", "pan", "abn", "acn", "stn", "reg no",
    "counter", "cashier", "operator", "terminal", "server", "bill no",
    "invoice no", "table", "order no", "token", "pax", "waiter", "host"
}

TABLE_HEADER_KEYWORDS = {
    "item", "items", "description", "particulars", "desc", "qty", "quantity",
    "rate", "price", "mrp", "amount", "amt", "sl no", "inline no", "hsn", "tax%"
}

TOTAL_KEYWORDS = {
    "total", "subtotal", "sub total", "grand total", "net amount", "net payable",
    "amount payable", "payable", "balance due", "gst", "tax", "cgst", "sgst",
    "igst", "vat", "round off"
}

PAYMENT_TENDER_KEYWORDS = {
    "cash", "change", "eftpos", "tender", "tendered", "visa", "mastercard",
    "master card", "amex", "debit", "credit", "card", "paid", "balance due",
    "cash tendered", "change due", "acct", "apprvl", "approval", "auth",
    "mid", "tid", "aid", "chip read", "entry method"
}


def extract_receipt_metadata(lines: List[dict]) -> ReceiptMetadata:
    """
    Extract merchant name, date, invoice number, financial summary totals, and currency.
    """
    meta = ReceiptMetadata()
    raw_full_text = "\n".join(l["text"] for l in lines)
    meta.currency = detect_currency_symbol(raw_full_text)

    date_regex = re.compile(
        r"\b(\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3})[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b"
    )

    # 1. Merchant Name: First valid merchant name in top 4 lines
    for item in lines[:4]:
        txt = item["text"].strip()
        lower = txt.lower()
        if len(txt) >= 3 and not any(kw in lower for kw in ["invoice", "bill", "tax", "welcome", "receipt", "cash memo", "duplicate", "abn", "gstin", "cin"]):
            if not any(lower.startswith(prefix) for prefix in ["tel", "ph", "date", "time", "order", "table"]):
                if any(w in lower for w in ["lettuce", "tomato", "potato", "onion", "apple", "banana", "bread", "milk", "rice", "atta", "dal", "kg", "net", "price", "rate", "qty"]):
                    continue
                clean_store = re.sub(r"[^\w\s&'-]", "", txt).strip()
                if is_valid_merchant_name(clean_store):
                    meta.store_name = clean_store
                    break

    # If no valid name found, leave blank so user is prompted to enter it
    if not meta.store_name:
        meta.store_name = ""

    # 2. Date extraction
    for item in lines:
        txt = item["text"]
        match = date_regex.search(txt)
        if match:
            raw_date = match.group(1)
            for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y"):
                try:
                    dt = datetime.strptime(raw_date, fmt)
                    meta.date = dt.strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if meta.date:
                break
            else:
                meta.date = raw_date
                break

    if not meta.date:
        meta.date = datetime.now().strftime("%Y-%m-%d")

    # 3. Totals, Tax, and Payment extraction
    for item in lines:
        txt = item["text"]
        lower = txt.lower()

        # Check for Grand Total
        if any(term in lower for term in ["grand total", "net total", "net payable", "total amount", "amount paid", "total:"]):
            match = re.findall(r"\b\d+(?:\.\d{1,2})?\b", txt)
            if match:
                val = clean_currency_token(match[-1])
                if val:
                    meta.grand_total = val

        elif "total" in lower and meta.grand_total is None and "subtotal" not in lower and "gst" not in lower:
            match = re.findall(r"\b\d+(?:\.\d{1,2})?\b", txt)
            if match:
                val = clean_currency_token(match[-1])
                if val:
                    meta.grand_total = val

        # Subtotal
        if "subtotal" in lower or "sub total" in lower:
            match = re.findall(r"\b\d+(?:\.\d{1,2})?\b", txt)
            if match:
                val = clean_currency_token(match[-1])
                if val:
                    meta.subtotal = val

        # Tax
        if any(term in lower for term in ["cgst", "sgst", "igst", "vat", "tax", "gst"]):
            match = re.findall(r"\b\d+(?:\.\d{1,2})?\b", txt)
            if match:
                val = clean_currency_token(match[-1])
                if val and "total" not in lower:
                    meta.tax = (meta.tax or 0.0) + val

        # Payment Tender info
        if "cash" in lower and not any(kw in lower for kw in ["cash memo", "cashier"]):
            meta.payment_method = "Cash"
            match = re.findall(r"\b\d+(?:\.\d{1,2})?\b", txt)
            if match:
                meta.tendered = clean_currency_token(match[-1])

        if "change" in lower:
            match = re.findall(r"\b\d+(?:\.\d{1,2})?\b", txt)
            if match:
                meta.change = clean_currency_token(match[-1])

        if any(k in lower for k in ["eftpos", "visa", "mastercard", "amex", "card", "debit", "credit"]):
            if not meta.payment_method:
                meta.payment_method = "Card"

    return meta


# ─── Robust Line Item Extraction ────────────────────────────────────────────
def parse_line_items(lines: List[dict]) -> List[ExtractedItem]:
    """
    Parse receipt lines into clean line items.
    Handles:
    - Weight/scale lines (e.g. '0.778 kg NET @ $5.99/kg $4.66') attaching to previous item.
    - Negative discount lines ('LOYALTY -15.00' -> -$15.00).
    - Skipping date lines ('DATE 06/01/2016').
    - Skipping tender lines ('CASH 50.00', 'CHANGE 25.80').
    - Unit measure protection (kg, g, L, ml, pcs, packs).
    """
    scale_regex = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:kg|g|lb|oz)\s*(?:net)?\s*@\s*[\$₹€£]?\s*(\d+(?:\.\d+)?)\s*/\s*(?:kg|g|lb|oz)",
        re.IGNORECASE
    )

    price_regex = re.compile(
        r"([-–—]?)\s*(?:Rs\.?|₹|INR|\$|£|€)?\s*([-–—]?)\s*(\d{1,6}(?:\.\d{1,2})?)\s*([-–—]?)\s*$"
    )

    date_regex = re.compile(
        r"\b(?:\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3})[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b"
    )

    items: List[ExtractedItem] = []
    seen = set()
    pending_item_name: Optional[str] = None
    pending_bbox: Optional[dict] = None

    for item in lines:
        raw_line = item["text"].strip()
        lower = raw_line.lower()

        if len(raw_line) < 2:
            continue

        # 1. Skip date lines
        if "date" in lower or date_regex.search(raw_line):
            continue

        # 1b. Skip invoice, bill, and transaction metadata headers
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in [
            "invoice", "tax invoice", "bill no", "receipt no", "token no", "order no",
            "table no", "gstin", "fssai", "cin", "pan no", "duplicate", "cash memo"
        ]):
            continue

        # 1c. Skip restaurant & retail service metadata
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in [
            "server", "station", "guests", "table", "reprint", "mgr", "manager", "seat", "chk", "order"
        ]):
            continue

        # 2. Skip contact info and phone numbers
        if any(lower.startswith(kw) or f" {kw}:" in lower or f" {kw} " in lower for kw in CONTACT_KEYWORDS):
            continue
        if re.search(r"\b(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]\d{4}\b", raw_line):
            continue

        # 3. Skip table column headers
        header_hit_count = sum(1 for kw in TABLE_HEADER_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", lower))
        if header_hit_count >= 2:
            continue

        # 4. Skip payment tender rows (CASH, CHANGE, EFTPOS, VISA)
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in PAYMENT_TENDER_KEYWORDS):
            continue

        # 4b. Skip suggested tips and gratuity lines (e.g. '18% 30.06' or 'Tip:')
        if re.search(r"(?:^\s*[\d.]+\s*%\s*|tip\b|gratuity\b|suggested\s+tip)", lower):
            continue

        # 4c. Skip card authorization and terminal details
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in [
            "approval", "auth", "acct", "aid", "appr", "seq", "entry method", "card #"
        ]):
            continue

        # 5. Skip summary rows (TOTAL, SUBTOTAL, ROUND OFF, TAX)
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in [
            "subtotal", "sub total", "grand total", "net total", "net amount", "total", "round off"
        ]) or re.search(r"\b(cgst|sgst|igst|vat|tax|gst)\s*[\d(%]", lower):
            continue

        # 6. Skip address and venue lines (including State + Zip code e.g. 'CA 90280' or 'FL 33176')
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in [
            "plot", "road", "street", "avenue", "floor", "flr", "mall", "lane", "pincode", "zip", "abn", "acn"
        ]) or re.search(r"\b[A-Z]{2}\.?\s+\d{5}(?:-\d{4})?\b", raw_line):
            continue

        # 7. Skip promotional non-priced tags e.g. 'SPECIAL'
        if lower in ["special", "promo", "on sale", "rollback"]:
            continue

        # 8. Skip thank-you, return policy, item counters, and pos terminal lines
        if any(kw in lower for kw in [
            "thank you", "visit again", "feedback", "saved today", "customer copy",
            "have a great day", "come again", "food universe", "returns must be",
            "return policy", "package for return", "number of items", "item count",
            "total items", "trx:", "term:", "register", "terminal"
        ]):
            continue

        # ── Check for Weight / Scale Line e.g. "0.778 kg NET @ $5.99/kg $4.66" ─
        scale_match = scale_regex.search(raw_line)
        if scale_match:
            qty = float(scale_match.group(1))
            unit_price = float(scale_match.group(2))

            # Look for price at the end
            p_match = price_regex.search(raw_line)
            if p_match and p_match.start() >= scale_match.end():
                line_total = float(p_match.group(3))
            else:
                line_total = round(qty * unit_price, 2)

            name = pending_item_name or "Produce Item"
            bbox = pending_bbox or item.get("bbox")

            items.append(ExtractedItem(
                item_name=name,
                amount=round(line_total, 2),
                quantity=qty,
                unit_price=round(unit_price, 2),
                confidence=round(item.get("confidence", 85.0) / 100.0, 3),
                bbox=bbox
            ))
            pending_item_name = None
            pending_bbox = None
            continue

        # ── Regular Price Extraction ──────────────────────────────────────────
        # Protect unit measures
        protected_line = re.sub(
            r"(\d+(?:\.\d+)?)\s*(kg|gm|g|ltr|litres|liter|l|ml|pcs|pc|pack|pk)\b",
            r"\1_\2_",
            raw_line,
            flags=re.IGNORECASE
        )

        p_match = price_regex.search(protected_line)
        if not p_match:
            # Fallback: check rightmost decimal number
            dec_matches = list(re.finditer(r"([-–—]?)\s*(?:Rs\.?|₹|INR|\$|£|€)?\s*([-–—]?)\s*(\d{1,6}\.\d{2})\s*([-–—]?)\s*", protected_line))
            if dec_matches:
                p_match = dec_matches[-1]

        if p_match:
            is_neg = bool(p_match.group(1) or p_match.group(2) or p_match.group(4))
            raw_val = float(p_match.group(3))
            amount_val = -raw_val if is_neg else raw_val

            raw_name = protected_line[:p_match.start()].strip()
            raw_name = re.sub(r"(\d+(?:\.\d+)?)_([a-zA-Z]+)_", r"\1 \2", raw_name)
            clean_name = re.sub(r"[^\w\s&/%().-]", "", raw_name).strip()
            clean_name = re.sub(r"\s+", " ", clean_name)
            clean_name = re.sub(r"^[-–—\s:]+|[-–—\s:]+$", "", clean_name)

            # Preserve negative discount lines (e.g. 'LOYALTY -15.00')
            if any(term in lower for term in ["loyalty", "discount", "special", "savings", "coupon", "voucher"]):
                if "-" in raw_line or "discount" in lower or "loyalty" in lower:
                    amount_val = -abs(amount_val)

            if len(clean_name) >= 2 and not clean_name.isdigit():
                # Check for quantity multipliers like "2 x 50.00"
                qty = 1.0
                unit_price = abs(amount_val)
                qty_match = re.search(r"\b(\d+)\s*[xX*@]\s*(\d+(?:\.\d{1,2})?)", raw_name)
                if qty_match:
                    try:
                        qty = float(qty_match.group(1))
                        unit_price = float(qty_match.group(2))
                    except ValueError:
                        pass

                dedup_key = f"{clean_name.lower()}_{amount_val}"
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    items.append(ExtractedItem(
                        item_name=clean_name,
                        amount=round(amount_val, 2),
                        quantity=qty,
                        unit_price=round(unit_price, 2),
                        confidence=round(item.get("confidence", 85.0) / 100.0, 3),
                        bbox=item.get("bbox")
                    ))
            pending_item_name = None
            pending_bbox = None
        else:
            # Line has no price: candidate produce item (e.g. 'LETTUCE ICEBERG')
            clean_candidate = re.sub(r"[^\w\s&/%().-]", "", raw_line).strip()
            if len(clean_candidate) >= 3 and not clean_candidate.isdigit():
                pending_item_name = clean_candidate
                pending_bbox = item.get("bbox")

    return items


# ─── Visual Bounding Box Annotation ──────────────────────────────────────────
def annotate_receipt_image(image: Image.Image, items: List[ExtractedItem], metadata: ReceiptMetadata) -> Image.Image:
    """
    Render visual bounding boxes on receipt image:
    - Green bounding boxes around extracted line items.
    """
    annotated = image.convert("RGBA").copy()
    overlay = Image.new("RGBA", annotated.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    w, h = annotated.size

    for item in items:
        if item.bbox:
            b = item.bbox
            x0, y0 = b["left"], b["top"]
            x1, y1 = x0 + b["width"], y0 + b["height"]

            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)

            draw.rectangle([x0, y0, x1, y1], outline=(16, 185, 129, 230), width=2)
            draw.rectangle([x0, y0, x1, y1], fill=(16, 185, 129, 30))

    annotated = Image.alpha_composite(annotated, overlay).convert("RGB")
    return annotated


# ─── Main Pipeline Interface ────────────────────────────────────────────────
def extract_receipt_items(
    image: Image.Image,
    use_trocr: bool = False
) -> Tuple[List[dict], str, dict, Optional[Image.Image]]:
    """
    End-to-end receipt extraction function.
    Returns:
        items_list: List of dict items
        raw_text: Raw OCR transcription
        metadata: Store, Date, Subtotal, Tax, Grand Total, Currency, Reconciliation
        annotated_image: PIL Image with visual bounding boxes
    """
    cv_color, enhanced_gray = preprocess_receipt_image(image)

    if use_trocr:
        lines = extract_lines_trocr(cv_color)
        if not lines:
            lines = extract_spatial_lines(enhanced_gray)
    else:
        lines = extract_spatial_lines(enhanced_gray)

    raw_text = "\n".join([line["text"] for line in lines])
    metadata = extract_receipt_metadata(lines)
    items = parse_line_items(lines)

    # Financial Reconciliation Check
    items_sum = round(sum(i.amount for i in items), 2)
    meta_dict = asdict(metadata)

    diff_subtotal = round(abs(items_sum - metadata.subtotal), 2) if metadata.subtotal else 999999
    diff_grand = round(abs(items_sum - metadata.grand_total), 2) if metadata.grand_total else 999999
    diff_pretax = round(abs(items_sum - (metadata.grand_total - (metadata.tax or 0))), 2) if (metadata.grand_total and metadata.tax) else 999999

    best_diff = min(diff_subtotal, diff_grand, diff_pretax)
    curr = metadata.currency

    if best_diff < 999999:
        meta_dict["reconciliation_diff"] = best_diff
        if best_diff <= 1.5 or (metadata.grand_total and (best_diff / max(metadata.grand_total, 1)) < 0.03):
            meta_dict["reconciliation_status"] = "Verified Match"
        else:
            meta_dict["reconciliation_status"] = f"Discrepancy: {curr}{best_diff:.2f} difference"
    else:
        meta_dict["reconciliation_status"] = "Estimated (No Printed Total)"
        meta_dict["reconciliation_diff"] = 0.0

    annotated_img = annotate_receipt_image(Image.fromarray(cv2.cvtColor(cv_color, cv2.COLOR_BGR2RGB)), items, metadata)
    items_dict_list = [item.to_dict() for item in items]
    return items_dict_list, raw_text, meta_dict, annotated_img