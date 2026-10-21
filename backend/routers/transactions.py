from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

import storage
from backend import db
from backend.auth import require_user
from backend.statements import matching, queries, recurring
from backend.statements.matching import MatchError
from categorizer import CANONICAL_CATEGORIES

router = APIRouter(prefix="/transactions", tags=["Transactions"], dependencies=[Depends(require_user)])

TxnType = Literal["expense", "income", "transfer_self", "transfer_person", "refund", "card_bill_payment",
                  "cash_withdrawal", "investment"]
assert set(TxnType.__args__) == set(db.TXN_TYPES)


class TxnPatch(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    category: Optional[str] = None
    txn_type: Optional[TxnType] = None
    excluded: Optional[bool] = None
    reviewed: Optional[bool] = None
    apply_to_payee: bool = False

    @field_validator("category")
    @classmethod
    def _cat(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CANONICAL_CATEGORIES:
            raise ValueError(f"category must be one of {CANONICAL_CATEGORIES}")
        return v


class LinkIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    receipt_id: str = Field(min_length=1, max_length=100)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except MatchError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("")
def list_transactions(frm: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
                      to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                      q: Optional[str] = Query(None, max_length=100), category: Optional[str] = None,
                      txn_type: Optional[TxnType] = None,
                      status: Optional[Literal["needs_review", "matched", "suggested", "excluded", "counted",
                                               "not_counted"]] = None,
                      limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return queries.list_transactions(frm, to, q, category, txn_type, status, limit, offset)


@router.get("/review")
def review():
    return queries.review_queue()


@router.get("/recurring")
def recurring_list():
    return recurring.recurring_payments()


@router.get("/receipt/{receipt_id}/candidates")
def candidates_for_receipt(receipt_id: str):
    return _call(matching.txn_candidates, receipt_id)


@router.get("/{txn_id}")
def get_one(txn_id: int):
    return _call(queries.get_transaction, txn_id)


@router.patch("/{txn_id}")
def patch(txn_id: int, body: TxnPatch):
    return _call(queries.patch, txn_id, body.category, body.txn_type, body.excluded, body.reviewed,
                 body.apply_to_payee)


@router.delete("/{txn_id}")
def delete(txn_id: int):
    """Remove one imported transaction (e.g. a wrongly parsed row). Linked receipts stay; only the link goes."""
    db.init()
    with storage.get_connection() as conn:
        cur = conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Transaction not found")
    return {"deleted": True}


@router.post("/{txn_id}/link")
def link(txn_id: int, body: LinkIn):
    _call(matching.link, txn_id, body.receipt_id)
    return queries.get_transaction(txn_id)


@router.delete("/{txn_id}/link")
def unlink(txn_id: int):
    _call(matching.unlink, txn_id)
    return queries.get_transaction(txn_id)


@router.post("/{txn_id}/candidates/{receipt_id}/reject")
def reject(txn_id: int, receipt_id: str):
    _call(matching.reject, txn_id, receipt_id)
    return {"rejected": True}


@router.get("/{txn_id}/receipt-candidates")
def receipt_candidates(txn_id: int):
    return _call(matching.receipt_candidates, txn_id)
