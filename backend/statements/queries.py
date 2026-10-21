"""Transaction listing, edits (with payee memory) and the review queue."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import storage
from backend import db
from backend.statements.matching import MatchError, _brief

_STATUS = {
    "needs_review": "t.needs_review = 1",
    "matched": "t.matched_receipt_id IS NOT NULL",
    "suggested": "EXISTS (SELECT 1 FROM match_candidates c WHERE c.transaction_id = t.id AND c.status = 'pending')"
                 " AND t.matched_receipt_id IS NULL",
    "excluded": "t.excluded = 1",
    "counted": db.counted_predicate(),
    "not_counted": f"NOT ({db.counted_predicate()})",
}
_SELECT = (f"SELECT t.*, ({db.counted_predicate()}) counted, r.store receipt_store, r.date receipt_date, "
           "r.total receipt_total, (SELECT COUNT(*) FROM match_candidates c WHERE c.transaction_id = t.id AND "
           "c.status = 'pending') suggestion_count FROM transactions t LEFT JOIN receipts r ON r.id = t.matched_receipt_id")


def _row(r) -> dict:
    d = dict(r)
    d["counted"] = bool(d["counted"])
    d["needs_review"] = bool(d["needs_review"])
    d["excluded"] = bool(d["excluded"])
    store, rdate, rtotal = d.pop("receipt_store"), d.pop("receipt_date"), d.pop("receipt_total")
    d["receipt"] = ({"id": d["matched_receipt_id"], "store": store, "date": rdate, "total": rtotal}
                    if d["matched_receipt_id"] else None)
    for k in ("dedupe_key", "statement_id", "account_key", "created_at"):
        d.pop(k, None)
    return d


def list_transactions(frm=None, to=None, q=None, category=None, txn_type=None, status=None, limit=100,
                      offset=0) -> dict:
    db.init()
    cond, params = [], []
    if frm:
        cond.append("t.date >= ?")
        params.append(frm)
    if to:
        cond.append("t.date <= ?")
        params.append(to)
    if q:
        like = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cond.append("(lower(t.description) LIKE ? ESCAPE '\\' OR lower(COALESCE(t.merchant,'')) LIKE ? ESCAPE '\\')")
        params += [like, like]
    if category:
        cond.append("t.category = ?")
        params.append(category)
    if txn_type:
        cond.append("t.txn_type = ?")
        params.append(txn_type)
    if status:
        cond.append(_STATUS[status])
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    with storage.get_connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transactions t{where}", params).fetchone()[0]
        rows = conn.execute(f"{_SELECT}{where} ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?",
                            params + [int(limit), int(offset)]).fetchall()
    return {"items": [_row(r) for r in rows], "total": total}


def get_transaction(txn_id: int) -> dict:
    db.init()
    with storage.get_connection() as conn:
        r = conn.execute(f"{_SELECT} WHERE t.id = ?", (txn_id,)).fetchone()
    if not r:
        raise MatchError("Transaction not found", 404)
    return _row(r)


def patch(txn_id: int, category: Optional[str] = None, txn_type: Optional[str] = None,
          excluded: Optional[bool] = None, reviewed: Optional[bool] = None, apply_to_payee: bool = False) -> dict:
    t = get_transaction(txn_id)
    sets, params = [], []
    if category is not None:
        sets += ["category = ?", "category_source = 'user'", "confidence = 1.0"]
        params.append(category)
    if txn_type is not None:
        sets.append("txn_type = ?")
        params.append(txn_type)
    if excluded is not None:
        sets.append("excluded = ?")
        params.append(int(excluded))
    if category is not None or txn_type is not None or reviewed:
        sets.append("needs_review = 0")
    with storage.get_connection() as conn:
        if sets:
            conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = ?", params + [txn_id])
        if apply_to_payee and t.get("payee_key") and (category is not None or txn_type is not None):
            cat = category or t["category"]
            typ = txn_type or t["txn_type"]
            conn.execute("INSERT INTO payee_rules (payee_key, category, txn_type, updated_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(payee_key) DO UPDATE SET category = excluded.category, "
                         "txn_type = excluded.txn_type, updated_at = excluded.updated_at",
                         (t["payee_key"], cat, typ, datetime.now().isoformat()))
            conn.execute("UPDATE transactions SET category = ?, txn_type = ?, category_source = 'user', "
                         "confidence = 1.0, needs_review = 0 WHERE payee_key = ? AND direction = 'debit' "
                         "AND category_source != 'user'", (cat, typ, t["payee_key"]))
        conn.commit()
    return get_transaction(txn_id)


def review_queue() -> dict:
    db.init()
    with storage.get_connection() as conn:
        rows = conn.execute(f"{_SELECT} WHERE t.needs_review = 1 AND t.matched_receipt_id IS NULL "
                            "ORDER BY t.date DESC LIMIT 200").fetchall()
        sugg = conn.execute(
            "SELECT c.transaction_id, c.receipt_id, c.score, r.store, r.date, r.total, r.currency FROM match_candidates c "
            "JOIN transactions t ON t.id = c.transaction_id JOIN receipts r ON r.id = c.receipt_id "
            "WHERE c.status = 'pending' AND t.matched_receipt_id IS NULL AND NOT EXISTS (SELECT 1 FROM transactions x "
            "WHERE x.matched_receipt_id = c.receipt_id) ORDER BY c.score DESC").fetchall()
        txn_rows = {}
        for s in sugg:
            if s["transaction_id"] not in txn_rows:
                tr = conn.execute("SELECT * FROM transactions WHERE id = ?", (s["transaction_id"],)).fetchone()
                txn_rows[s["transaction_id"]] = _brief(dict(tr))
    matches = [{"transaction": txn_rows[s["transaction_id"]],
                "receipt": {"id": s["receipt_id"], "store": s["store"], "date": s["date"], "total": s["total"],
                            "currency": s["currency"]}, "score": s["score"]} for s in sugg]
    needs = [_row(r) for r in rows]
    return {"needs_review": needs, "match_suggestions": matches, "count": len(needs) + len(matches)}
