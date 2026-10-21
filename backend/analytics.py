"""Spending analytics over a date range, always scoped to a single currency (never summed across currencies)."""

from __future__ import annotations

import calendar
from datetime import date as date_cls
from typing import List, Optional

import storage
from backend import db


# Split bills count only your share (my_share is NULL when a receipt isn't split)
_RT = "COALESCE(r.my_share, r.total)"
_LA = "(li.amount * li.share_weight)"


def _today() -> date_cls:
    return date_cls.today()


def _where(frm: Optional[str], to: Optional[str], currency: Optional[str], alias: str = "r"):
    cond, params = [], []
    if frm:
        cond.append(f"{alias}.date >= ?")
        params.append(frm)
    if to:
        cond.append(f"{alias}.date <= ?")
        params.append(to)
    if currency:
        cond.append(f"{alias}.currency = ?")
        params.append(currency)
    return (" WHERE " + " AND ".join(cond)) if cond else "", params


def dominant_currency(frm: Optional[str] = None, to: Optional[str] = None) -> str:
    db.init()
    w, p = _where(frm, to, None, "d")
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT currency, COUNT(*) c FROM spend_docs d{w} GROUP BY currency ORDER BY c DESC",
                            p).fetchall()
    return rows[0]["currency"] if rows else "INR"


def currencies() -> List[str]:
    db.init()
    with storage.get_connection() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT currency FROM spend_docs ORDER BY currency")]


def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


# ─── Primitive queries (also used as chat tools) ────────────────────────────
# All spend queries read the spend_docs / spend_lines views: receipts plus statement payments that are
# counted spending and not linked to a receipt, so a purchase with both a receipt and a bank row counts once.
def total_spend(frm=None, to=None, currency="INR", category: Optional[str] = None) -> dict:
    db.init()
    with storage.get_connection() as conn:
        if category:
            w, p = _where(frm, to, currency, "l")
            w += (" AND " if w else " WHERE ") + "l.category = ?"
            row = conn.execute(f"SELECT COALESCE(SUM(l.amount),0) t, COUNT(DISTINCT CASE WHEN l.source = 'receipt' "
                               f"THEN l.doc_id END) c, COUNT(DISTINCT CASE WHEN l.source = 'statement' THEN l.doc_id "
                               f"END) s FROM spend_lines l{w}", p + [category]).fetchone()
        else:
            w, p = _where(frm, to, currency, "d")
            row = conn.execute(f"SELECT COALESCE(SUM(d.amount),0) t, COUNT(CASE WHEN d.source = 'receipt' THEN 1 END) c,"
                               f" COUNT(CASE WHEN d.source = 'statement' THEN 1 END) s FROM spend_docs d{w}",
                               p).fetchone()
    return {"total": round(row["t"], 2), "receipt_count": row["c"], "transaction_count": row["s"],
            "payment_count": row["c"] + row["s"], "currency": currency, "from": frm, "to": to, "category": category}


def category_breakdown(frm=None, to=None, currency="INR") -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency, "l")
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT l.category, SUM(l.amount) a, COUNT(*) n FROM spend_lines l{w} "
                            f"GROUP BY l.category ORDER BY a DESC", p).fetchall()
    return [{"category": r["category"], "amount": round(r["a"], 2), "count": r["n"]} for r in rows]


def merchant_breakdown(frm=None, to=None, currency="INR", limit: int = 10,
                       category: Optional[str] = None, query: Optional[str] = None) -> List[dict]:
    db.init()
    with storage.get_connection() as conn:
        if category:
            w, p = _where(frm, to, currency, "l")
            w += (" AND " if w else " WHERE ") + "l.category = ?"
            rows = conn.execute(f"SELECT l.merchant, SUM(l.amount) a, COUNT(DISTINCT l.doc_id) n FROM spend_lines l{w} "
                                f"GROUP BY lower(l.merchant) ORDER BY a DESC LIMIT ?",
                                p + [category, int(limit)]).fetchall()
        else:
            w, p = _where(frm, to, currency, "d")
            if query:
                w += (" AND " if w else " WHERE ") + "lower(d.merchant) LIKE ? ESCAPE '\\'"
                p.append("%" + query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            rows = conn.execute(f"SELECT d.merchant, SUM(d.amount) a, COUNT(*) n FROM spend_docs d{w} "
                                f"GROUP BY lower(d.merchant) ORDER BY a DESC LIMIT ?", p + [int(limit)]).fetchall()
    return [{"merchant": r["merchant"], "amount": round(r["a"], 2), "count": r["n"]} for r in rows]


def monthly_trend(frm=None, to=None, currency="INR", months: Optional[int] = None) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency, "d")
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT d.month, SUM(d.amount) a, COUNT(*) n FROM spend_docs d{w} GROUP BY d.month "
                            f"ORDER BY d.month", p).fetchall()
    out = [{"month": r["month"], "amount": round(r["a"], 2), "count": r["n"]} for r in rows]
    return out[-months:] if months else out


def search_items(query: str, frm=None, to=None, currency: Optional[str] = None, limit: int = 50) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    w += (" AND " if w else " WHERE ") + "li.item_name LIKE ? ESCAPE '\\'"
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT li.item_name, li.category, {_LA} AS amount, r.store, r.date, r.currency "
                            f"FROM line_items li JOIN receipts r ON r.id = li.receipt_id{w} "
                            f"ORDER BY r.date DESC LIMIT ?", p + [like, int(limit)]).fetchall()
    return [dict(r) for r in rows]


def top_items(frm=None, to=None, currency="INR", limit: int = 10) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    w += (" AND " if w else " WHERE ") + "li.share_weight > 0"
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT li.item_name, li.category, {_LA} AS amount, r.store, r.date FROM line_items li "
                            f"JOIN receipts r ON r.id = li.receipt_id{w} ORDER BY {_LA} DESC LIMIT ?",
                            p + [int(limit)]).fetchall()
    return [dict(r) for r in rows]


def budget_status(month: Optional[str] = None, currency="INR") -> List[dict]:
    month = month or _today().strftime("%Y-%m")
    frm, to = _month_bounds(month)
    spent = {c["category"]: c["amount"] for c in category_breakdown(frm, to, currency)}
    out = []
    for cat, limit in db.budgets_map(currency).items():
        s = spent.get(cat, 0.0)
        pct = round(s / limit * 100, 1) if limit > 0 else 0.0
        status = "exceeded" if pct >= 100 else "warning" if pct >= 75 else "ok"
        out.append({"category": cat, "limit": limit, "spent": round(s, 2), "remaining": round(limit - s, 2),
                    "percentage": pct, "status": status, "month": month, "currency": currency})
    return sorted(out, key=lambda b: b["percentage"], reverse=True)


def forecast(currency="INR", today: Optional[date_cls] = None) -> dict:
    """Linear run-rate projection for the current calendar month using the real day of month."""
    today = today or _today()
    month = today.strftime("%Y-%m")
    days = calendar.monthrange(today.year, today.month)[1]
    spent = total_spend(f"{month}-01", today.isoformat(), currency)["total"]
    daily = spent / max(today.day, 1)
    return {"month": month, "spent": spent, "day": today.day, "days_in_month": days,
            "daily_average": round(daily, 2), "projected_total": round(daily * days, 2), "currency": currency}


def month_over_month(month: Optional[str] = None, currency="INR") -> dict:
    month = month or _today().strftime("%Y-%m")
    prev = _prev_month(month)
    cur_t = total_spend(*_month_bounds(month), currency)["total"]
    prev_t = total_spend(*_month_bounds(prev), currency)["total"]
    change = round((cur_t - prev_t) / prev_t * 100, 1) if prev_t > 0 else None
    return {"month": month, "previous_month": prev, "current": cur_t, "previous": prev_t, "change_pct": change}


def summary(frm: Optional[str] = None, to: Optional[str] = None, currency: Optional[str] = None) -> dict:
    currency = storage.to_iso_currency(currency) if currency else dominant_currency(frm, to)
    tot = total_spend(frm, to, currency)
    ref_month = (to or _today().isoformat())[:7]
    return {
        "currency": currency,
        "currencies": currencies(),
        "from": frm,
        "to": to,
        "total": tot["total"],
        "receipt_count": tot["receipt_count"],
        "transaction_count": tot["transaction_count"],
        "payment_count": tot["payment_count"],
        "average_per_receipt": round(tot["total"] / tot["payment_count"], 2) if tot["payment_count"] else 0.0,
        "source_mix": source_mix(frm, to, currency),
        "per_category": category_breakdown(frm, to, currency),
        "per_merchant": merchant_breakdown(frm, to, currency, 10),
        "top_items": top_items(frm, to, currency, 10),
        "monthly_trend": monthly_trend(frm, to, currency),
        "month_over_month": month_over_month(ref_month, currency),
        "forecast": forecast(currency),
        "budget_status": budget_status(ref_month, currency),
        "split_count": split_count(frm, to, currency),
    }


def split_count(frm=None, to=None, currency="INR") -> int:
    db.init()
    w, p = _where(frm, to, currency)
    w += (" AND " if w else " WHERE ") + "r.my_share IS NOT NULL"
    with storage.get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM receipts r{w}", p).fetchone()[0]


def month_snapshot(month: str, currency: str) -> dict:
    """Numbers fed to the insights generator for one month."""
    frm, to = _month_bounds(month)
    tot = total_spend(frm, to, currency)
    return {
        "month": month, "currency": currency, "total": tot["total"], "receipt_count": tot["receipt_count"],
        "payment_count": tot["payment_count"], "source_mix": source_mix(frm, to, currency),
        "per_category": category_breakdown(frm, to, currency),
        "per_merchant": merchant_breakdown(frm, to, currency, 5),
        "top_items": top_items(frm, to, currency, 5),
        "month_over_month": month_over_month(month, currency),
        "budget_status": budget_status(month, currency),
    }


def receipt_fingerprint(month: str, currency: str) -> str:
    """Hash of counted spend lines (receipts + bank payments) and budgets for cache invalidation."""
    import hashlib

    db.init()
    frm, to = _month_bounds(month)
    w, p = _where(frm, to, currency, "l")
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT l.doc_id, l.category, l.amount FROM spend_lines l{w} "
                            f"ORDER BY l.doc_id, l.category, l.amount", p).fetchall()
    budgets = sorted(db.budgets_map(currency).items())
    raw = "|".join(f"{r['doc_id']}:{r['category']}:{r['amount']}" for r in rows) + repr(budgets)
    return hashlib.sha256(raw.encode()).hexdigest()


def source_mix(frm=None, to=None, currency="INR") -> dict:
    """How much of the spend came from receipts vs bank/UPI statements, and how many pairs were de-duplicated."""
    db.init()
    w, p = _where(frm, to, currency, "d")
    tw, tp = _where(frm, to, currency, "t")
    with storage.get_connection() as conn:
        rows = {r["source"]: r for r in conn.execute(
            f"SELECT d.source, COALESCE(SUM(d.amount),0) a, COUNT(*) n FROM spend_docs d{w} GROUP BY d.source", p)}
        extra = conn.execute(
            f"SELECT COUNT(CASE WHEN t.matched_receipt_id IS NOT NULL THEN 1 END) linked, "
            f"COUNT(CASE WHEN t.needs_review = 1 AND t.matched_receipt_id IS NULL THEN 1 END) review "
            f"FROM transactions t{tw}", tp).fetchone()

    def part(k):
        r = rows.get(k)
        return {"amount": round(r["a"], 2) if r else 0.0, "count": r["n"] if r else 0}

    stmt = part("statement")
    refunds = part("refund")
    # Bank/UPI amount is net of refunds so receipts + statements always equals the total shown.
    stmt["amount"] = round(stmt["amount"] + refunds["amount"], 2)
    return {"receipts": part("receipt"), "statements": stmt, "refunds": {"amount": round(-refunds["amount"], 2),
            "count": refunds["count"]}, "linked_count": extra["linked"], "needs_review_count": extra["review"]}


def payee_spend(frm=None, to=None, currency="INR", query: Optional[str] = None, limit: int = 10) -> List[dict]:
    return merchant_breakdown(frm, to, currency, limit, None, query)
