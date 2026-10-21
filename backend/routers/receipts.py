import os
from datetime import date as date_cls
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

import storage
from backend import db
from backend.auth import require_user
from categorizer import CANONICAL_CATEGORIES

router = APIRouter(prefix="/receipts", tags=["Receipts"], dependencies=[Depends(require_user)])

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".pdf": "application/pdf"}


class ItemIn(BaseModel):
    item_name: str = Field(min_length=1, max_length=300)
    amount: float
    category: str = "Other"
    quantity: Optional[float] = 1.0
    unit_price: Optional[float] = None
    discount: Optional[float] = 0.0
    confidence: Optional[float] = 1.0
    original_category: Optional[str] = None

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        if v not in CANONICAL_CATEGORIES:
            raise ValueError(f"category must be one of {CANONICAL_CATEGORIES}")
        return v


class ReceiptIn(BaseModel):
    merchant_name: str = Field(default="", max_length=300)
    date: Optional[str] = None
    time: Optional[str] = Field(default=None, max_length=20)
    merchant_address: Optional[str] = Field(default=None, max_length=500)
    invoice_number: Optional[str] = Field(default=None, max_length=100)
    currency: str = "INR"
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    payment_method: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default="", max_length=2000)
    items: List[ItemIn] = Field(default_factory=list)
    upload_id: Optional[str] = None
    engine_used: Optional[str] = Field(default=None, max_length=50)
    reconciliation_status: Optional[str] = Field(default=None, max_length=50)
    raw_text: Optional[str] = Field(default=None, max_length=50000)
    force: bool = False

    @field_validator("date")
    @classmethod
    def _date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        try:
            return date_cls.fromisoformat(v).isoformat()
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")


def _public(rec: dict) -> dict:
    rec = dict(rec)
    rec["has_file"] = bool(rec.pop("file_path", None))
    return rec


def _record_corrections(body: ReceiptIn) -> None:
    for it in body.items:
        if it.original_category and it.original_category != it.category:
            storage.record_user_correction(it.item_name, it.category, body.merchant_name)


def _save(body: ReceiptIn, receipt_id: Optional[str] = None, existing: Optional[dict] = None) -> str:
    file_path = db.resolve_upload(body.upload_id)
    file_hash = os.path.basename(file_path).split(".")[0] if file_path else None
    if existing and not file_path:
        file_path, file_hash = existing.get("file_path"), existing.get("file_hash")
    return storage.save_receipt(
        store_name=body.merchant_name, items=[i.model_dump(exclude={"original_category"}) for i in body.items],
        scan_date=body.date, grand_total=body.total, currency=body.currency, payment_method=body.payment_method or "",
        notes=body.notes or "", subtotal=body.subtotal or 0.0, tax=body.tax or 0.0, receipt_id=receipt_id,
        merchant_address=body.merchant_address, time=body.time, invoice_number=body.invoice_number,
        file_path=file_path, file_hash=file_hash,
        engine_used=body.engine_used or (existing or {}).get("engine_used"),
        reconciliation_status=body.reconciliation_status or (existing or {}).get("reconciliation_status"),
        raw_text=body.raw_text if body.raw_text is not None else (existing or {}).get("raw_text"),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_receipt(body: ReceiptIn):
    if not body.force:
        file_path = db.resolve_upload(body.upload_id)
        file_hash = os.path.basename(file_path).split(".")[0] if file_path else None
        dup = db.find_duplicate(file_hash, body.merchant_name, body.date or "", body.total)
        if dup:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                {"message": "This receipt looks like one you already saved.", "duplicate_of": dup})
    rid = _save(body)
    _record_corrections(body)
    return {"id": rid}


@router.get("")
def list_all(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, q: Optional[str] = None,
             category: Optional[str] = None):
    db.init()
    cond, params = [], []
    if frm:
        cond.append("r.date >= ?")
        params.append(frm)
    if to:
        cond.append("r.date <= ?")
        params.append(to)
    if q:
        like = "%" + q.strip().lower() + "%"
        cond.append("(lower(r.store) LIKE ? OR EXISTS (SELECT 1 FROM line_items l WHERE l.receipt_id = r.id "
                    "AND lower(l.item_name) LIKE ?))")
        params += [like, like]
    if category:
        cond.append("EXISTS (SELECT 1 FROM line_items l WHERE l.receipt_id = r.id AND l.category = ?)")
        params.append(category)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    sql = (f"SELECT r.id, r.date, r.store, r.total, r.currency, r.engine_used, r.reconciliation_status, "
           f"(r.file_path IS NOT NULL) has_file, (SELECT COUNT(*) FROM line_items l WHERE l.receipt_id = r.id) "
           f"item_count, (SELECT group_concat(DISTINCT l.category) FROM line_items l WHERE l.receipt_id = r.id) "
           f"categories FROM receipts r{where} ORDER BY r.date DESC, r.created_at DESC")
    with storage.get_connection() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["has_file"] = bool(r["has_file"])
        r["categories"] = (r["categories"] or "").split(",") if r["categories"] else []
    return rows


def _get_or_404(receipt_id: str) -> dict:
    rec = storage.get_receipt(receipt_id)
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return rec


@router.get("/{receipt_id}")
def get_one(receipt_id: str):
    return _public(_get_or_404(receipt_id))


@router.put("/{receipt_id}")
def update(receipt_id: str, body: ReceiptIn):
    existing = _get_or_404(receipt_id)
    _save(body, receipt_id=receipt_id, existing=existing)
    _record_corrections(body)
    return _public(_get_or_404(receipt_id))


@router.delete("/{receipt_id}")
def delete(receipt_id: str):
    existing = _get_or_404(receipt_id)
    storage.delete_receipt(receipt_id)
    path = existing.get("file_path")
    if path and not db.find_duplicate(existing.get("file_hash")) and os.path.exists(path):
        os.remove(path)
    return {"deleted": receipt_id}


@router.get("/{receipt_id}/file")
def get_file(receipt_id: str):
    rec = _get_or_404(receipt_id)
    path = rec.get("file_path")
    if not path or not os.path.exists(path) or os.path.dirname(os.path.abspath(path)) != os.path.abspath(db.files_dir()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No source file stored for this receipt")
    return FileResponse(path, media_type=_MEDIA.get(os.path.splitext(path)[1], "application/octet-stream"))
