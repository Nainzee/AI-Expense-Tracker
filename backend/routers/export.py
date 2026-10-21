import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

import storage
from backend import db
from backend.auth import require_user

router = APIRouter(prefix="/export", tags=["Export"], dependencies=[Depends(require_user)])

_COLUMNS = ["receipt_id", "date", "merchant", "currency", "receipt_total", "item_name", "category", "quantity",
            "unit_price", "discount", "amount"]


def _safe(v):
    """Neutralise spreadsheet formula injection."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@router.get("/csv")
def export_csv(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    db.init()
    cond, params = [], []
    if frm:
        cond.append("r.date >= ?")
        params.append(frm)
    if to:
        cond.append("r.date <= ?")
        params.append(to)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    with storage.get_connection() as conn:
        rows = conn.execute(
            "SELECT r.id, r.date, r.store, r.currency, r.total, l.item_name, l.category, l.quantity, l.unit_price, "
            f"l.discount, l.amount FROM receipts r JOIN line_items l ON l.receipt_id = r.id{where} "
            "ORDER BY r.date DESC, r.id, l.id", params).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_COLUMNS)
    for r in rows:
        w.writerow([_safe(v) for v in tuple(r)])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="expenses.csv"'})
