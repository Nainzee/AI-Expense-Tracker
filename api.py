"""
Production REST API for Receipt Scanner & Expense Intelligence.

Built with FastAPI & Pydantic v2.
Interactive documentation available at /docs and /redoc.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional
from datetime import datetime

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from PIL import Image

from ocr_pipeline import extract_receipt_items
from categorizer import categorize_items, CANONICAL_CATEGORIES, retrain_personalized_classifier
from monthly_summary import (
    save_receipt,
    get_receipt,
    delete_receipt,
    list_receipts,
    get_monthly_data,
    generate_insights,
    get_available_months,
)
from storage import record_user_correction
from statement_matcher import parse_bank_statement_csv, match_receipts_to_statement

# ─── App Initialization ──────────────────────────────────────────────────────
app = FastAPI(
    title="Smart Receipt Scanner & Expense Intelligence API",
    description=(
        "High-performance computer vision and NLP API for automated receipt digitization, "
        "spatial line-item extraction, deep learning transcription, and automated expense categorization."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware for mobile/web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic Data Models ───────────────────────────────────────────────────
class LineItemModel(BaseModel):
    item_name: str = Field(..., json_schema_extra={"example": "Basmati Rice 5kg"})
    amount: float = Field(..., json_schema_extra={"example": 340.0})
    category: str = Field(default="Other", json_schema_extra={"example": "Food"})
    confidence: float = Field(default=0.90, ge=0.0, le=1.0, json_schema_extra={"example": 0.95})
    quantity: float = Field(default=1.0, ge=0.0, json_schema_extra={"example": 1.0})
    unit_price: Optional[float] = Field(default=None, json_schema_extra={"example": 340.0})


class ReceiptMetadataModel(BaseModel):
    store_name: str = Field(default="", json_schema_extra={"example": "Supermart Retail"})
    date: str = Field(default="", json_schema_extra={"example": "2026-10-18"})
    invoice_number: Optional[str] = Field(default="", json_schema_extra={"example": "INV-84920"})
    subtotal: Optional[float] = Field(default=None, json_schema_extra={"example": 1285.0})
    tax: Optional[float] = Field(default=None, json_schema_extra={"example": 64.24})
    grand_total: Optional[float] = Field(default=None, json_schema_extra={"example": 1349.24})
    currency: str = Field(default="₹", json_schema_extra={"example": "$"})
    payment_method: Optional[str] = Field(default="", json_schema_extra={"example": "Card"})
    reconciliation_status: str = Field(default="Unverified", json_schema_extra={"example": "Verified Match"})
    reconciliation_diff: float = Field(default=0.0, json_schema_extra={"example": 0.0})


class ScanResponseModel(BaseModel):
    success: bool
    metadata: ReceiptMetadataModel
    items: List[LineItemModel]
    total_amount: float
    item_count: int
    raw_text: str


class SaveReceiptRequest(BaseModel):
    store_name: str = Field(..., json_schema_extra={"example": "Supermart Retail"})
    items: List[LineItemModel]
    scan_date: Optional[str] = Field(default=None, json_schema_extra={"example": "2026-10-18"})
    subtotal: float = 0.0
    tax: float = 0.0
    grand_total: Optional[float] = None
    currency: Optional[str] = Field(default="₹", json_schema_extra={"example": "$"})
    payment_method: Optional[str] = "Card"
    notes: Optional[str] = ""


class ReceiptRecord(BaseModel):
    id: str
    store: str
    date: str
    month: str
    total: float
    subtotal: float
    tax: float
    currency: str = Field(default="₹", json_schema_extra={"example": "$"})
    payment_method: str
    notes: str
    created_at: str
    items: List[Dict[str, Any]]


class MonthlyAnalyticsResponse(BaseModel):
    month: str
    receipt_count: int
    grand_total: float
    avg_per_receipt: float
    top_category: str
    mom_change_pct: Optional[float] = None
    category_totals: Dict[str, float]
    category_counts: Dict[str, int]
    weekly: Dict[str, float]
    insights: List[str]


# ─── API Endpoints ──────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
@app.get("/api/v1/health", tags=["System"])
async def health_check():
    """Health status and capabilities check."""

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "features": {
            "spatial_tesseract_ocr": True,
            "trocr_transformer": True,
            "hybrid_categorizer": True,
            "temperature_scaling_calibration": True,
            "active_learning_personalization": True,
            "bank_statement_reconciliation": True,
        }
    }


@app.get("/api/v1/categories", tags=["Taxonomy"])
async def list_categories():
    """List all supported spending categories."""
    return {"categories": CANONICAL_CATEGORIES}


@app.post("/api/v1/scan", response_model=ScanResponseModel, tags=["Receipt Digitization"])
async def scan_receipt_endpoint(
    file: UploadFile = File(..., description="Receipt image file (PNG, JPG, JPEG)"),
    use_trocr: bool = Form(default=False, description="Set True to invoke TrOCR Vision Transformer")
):
    """
    Digitize receipt image:
    - Preprocesses image (dynamic upscaling, contrast equalization, auto-deskew).
    - Spatial line clustering or deep learning line transcription.
    - Extracts merchant, date, financial totals, line items, and runs hybrid categorization.
    """
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image file: {str(exc)}"
        )

    try:
        raw_items, raw_text, meta_dict, _ = extract_receipt_items(image, use_trocr=use_trocr)
        categorized = categorize_items(raw_items)
        calc_total = sum(i["amount"] for i in categorized)

        return ScanResponseModel(
            success=True,
            metadata=ReceiptMetadataModel(**meta_dict),
            items=[LineItemModel(**item) for item in categorized],
            total_amount=round(calc_total, 2),
            item_count=len(categorized),
            raw_text=raw_text
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OCR Extraction failure: {str(exc)}"
        )


@app.post("/api/v1/receipts", tags=["Receipt Management"], status_code=status.HTTP_201_CREATED)
async def create_receipt_endpoint(payload: SaveReceiptRequest):
    """Save a validated receipt record into the expense tracker database and record user corrections."""
    try:
        receipt_id = save_receipt(
            store_name=payload.store_name,
            items=[item.model_dump() for item in payload.items],
            scan_date=payload.scan_date,
            subtotal=payload.subtotal,
            tax=payload.tax,
            grand_total=payload.grand_total,
            currency=payload.currency or "₹",
            payment_method=payload.payment_method or "",
            notes=payload.notes or "",
        )

        # Active Learning: record items to user corrections memory
        for it in payload.items:
            record_user_correction(it.item_name, it.category, payload.store_name)

        return {
            "success": True,
            "receipt_id": receipt_id,
            "message": f"Receipt {receipt_id} recorded successfully."
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record receipt: {str(exc)}"
        )


@app.get("/api/v1/receipts", response_model=List[ReceiptRecord], tags=["Receipt Management"])
async def list_receipts_endpoint(
    month: Optional[str] = None,
    search: Optional[str] = None
):
    """Query stored receipts filtered by month key (YYYY-MM) and/or text search."""
    records = list_receipts(month_key=month, search_query=search)
    return [ReceiptRecord(**r) for r in records]


@app.get("/api/v1/receipts/{receipt_id}", response_model=ReceiptRecord, tags=["Receipt Management"])
async def get_receipt_endpoint(receipt_id: str):
    """Retrieve details of a single receipt by ID."""
    rec = get_receipt(receipt_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Receipt with ID '{receipt_id}' not found."
        )
    return ReceiptRecord(**rec)


@app.delete("/api/v1/receipts/{receipt_id}", tags=["Receipt Management"])
async def delete_receipt_endpoint(receipt_id: str):
    """Delete a receipt record by ID."""
    deleted = delete_receipt(receipt_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Receipt with ID '{receipt_id}' not found."
        )
    return {"success": True, "message": f"Receipt '{receipt_id}' removed."}


@app.get("/api/v1/analytics/monthly", response_model=MonthlyAnalyticsResponse, tags=["Analytics"])
async def monthly_analytics_endpoint(month: Optional[str] = None):
    """Retrieve comprehensive spending statistics, MoM trends, budget alerts, and AI insights."""
    data = get_monthly_data(month)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No transaction records found for month '{month or 'latest'}'."
        )

    insights = generate_insights(data)
    return MonthlyAnalyticsResponse(
        month=data["month"],
        receipt_count=data["receipt_count"],
        grand_total=data["grand_total"],
        avg_per_receipt=data["avg_per_receipt"],
        top_category=data["top_category"],
        mom_change_pct=data["mom_change_pct"],
        category_totals=data["category_totals"],
        category_counts=data["category_counts"],
        weekly=data["weekly"],
        insights=insights
    )


@app.post("/api/v1/reconcile", tags=["Reconciliation"])
async def reconcile_statement_endpoint(
    file: UploadFile = File(..., description="Bank or UPI statement CSV file"),
    month: Optional[str] = None
):
    """Reconcile scanned receipts against an uploaded bank or UPI statement CSV."""
    try:
        content = await file.read()
        csv_text = content.decode("utf-8", errors="ignore")
        txns = parse_bank_statement_csv(csv_text)
        receipts = list_receipts(month_key=month)
        matches = match_receipts_to_statement(receipts, txns)
        return {
            "success": True,
            "total_receipts": len(receipts),
            "total_transactions": len(txns),
            "matches": [m.to_dict() for m in matches]
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconciliation failure: {str(exc)}"
        )


@app.post("/api/v1/personalize/retrain", tags=["Active Learning"])
async def retrain_personalization_endpoint():
    """Trigger lightweight on-device retraining on accumulated user corrections."""
    try:
        res = retrain_personalized_classifier()
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retraining error: {str(exc)}"
        )
