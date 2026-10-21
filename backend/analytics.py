"""Spending analytics over a date range, always scoped to a single currency (never summed across currencies)."""

from __future__ import annotations

import calendar
from datetime import date as date_cls
from typing import List, Optional

import storage
from backend import db


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
    w, p = _where(frm, to, None)
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT currency, COUNT(*) c FROM receipts r{w} GROUP BY currency ORDER BY c DESC", p).fetchall()
    return rows[0]["currency"] if rows else "INR"


def currencies() -> List[str]:
    db.init()
    with storage.get_connection() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT currency FROM receipts ORDER BY currency")]


def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


# ─── Primitive queries (also used as chat tools) ────────────────────────────
def total_spend(frm=None, to=None, currency="INR", category: Optional[str] = None) -> dict:
    db.init()
    with storage.get_connection() as conn:
        if category:
            w, p = _where(frm, to, currency)
            w += (" AND " if w else " WHERE ") + "li.category = ?"
            row = conn.execute(f"SELECT COALESCE(SUM(li.amount),0) t, COUNT(DISTINCT r.id) c FROM line_items li "
                               f"JOIN receipts r ON r.id = li.receipt_id{w}", p + [category]).fetchone()
        else:
            w, p = _where(frm, to, currency)
            row = conn.execute(f"SELECT COALESCE(SUM(r.total),0) t, COUNT(*) c FROM receipts r{w}", p).fetchone()
    return {"total": round(row["t"], 2), "receipt_count": row["c"], "currency": currency, "from": frm, "to": to,
            "category": category}


def category_breakdown(frm=None, to=None, currency="INR") -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT li.category, SUM(li.amount) a, COUNT(*) n FROM line_items li "
                            f"JOIN receipts r ON r.id = li.receipt_id{w} GROUP BY li.category ORDER BY a DESC", p).fetchall()
    return [{"category": r["category"], "amount": round(r["a"], 2), "count": r["n"]} for r in rows]


def merchant_breakdown(frm=None, to=None, currency="INR", limit: int = 10,
                       category: Optional[str] = None) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    with storage.get_connection() as conn:
        if category:
            w += (" AND " if w else " WHERE ") + "li.category = ?"
            rows = conn.execute(f"SELECT r.store, SUM(li.amount) a, COUNT(DISTINCT r.id) n FROM line_items li "
                                f"JOIN receipts r ON r.id = li.receipt_id{w} GROUP BY r.store ORDER BY a DESC LIMIT ?",
                                p + [category, int(limit)]).fetchall()
        else:
            rows = conn.execute(f"SELECT r.store, SUM(r.total) a, COUNT(*) n FROM receipts r{w} GROUP BY r.store "
                                f"ORDER BY a DESC LIMIT ?", p + [int(limit)]).fetchall()
    return [{"merchant": r["store"], "amount": round(r["a"], 2), "count": r["n"]} for r in rows]


def monthly_trend(frm=None, to=None, currency="INR", months: Optional[int] = None) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT r.month, SUM(r.total) a, COUNT(*) n FROM receipts r{w} GROUP BY r.month "
                            f"ORDER BY r.month", p).fetchall()
    out = [{"month": r["month"], "amount": round(r["a"], 2), "count": r["n"]} for r in rows]
    return out[-months:] if months else out


def search_items(query: str, frm=None, to=None, currency: Optional[str] = None, limit: int = 50) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    w += (" AND " if w else " WHERE ") + "li.item_name LIKE ? ESCAPE '\\'"
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT li.item_name, li.category, li.amount, r.store, r.date, r.currency "
                            f"FROM line_items li JOIN receipts r ON r.id = li.receipt_id{w} "
                            f"ORDER BY r.date DESC LIMIT ?", p + [like, int(limit)]).fetchall()
    return [dict(r) for r in rows]


def top_items(frm=None, to=None, currency="INR", limit: int = 10) -> List[dict]:
    db.init()
    w, p = _where(frm, to, currency)
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT li.item_name, li.category, li.amount, r.store, r.date FROM line_items li "
                            f"JOIN receipts r ON r.id = li.receipt_id{w} ORDER BY li.amount DESC LIMIT ?",
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
        "average_per_receipt": round(tot["total"] / tot["receipt_count"], 2) if tot["receipt_count"] else 0.0,
        "per_category": category_breakdown(frm, to, currency),
        "per_merchant": merchant_breakdown(frm, to, currency, 10),
        "top_items": top_items(frm, to, currency, 10),
        "monthly_trend": monthly_trend(frm, to, currency),
        "month_over_month": month_over_month(ref_month, currency),
        "forecast": forecast(currency),
        "budget_status": budget_status(ref_month, currency),
    }


def month_snapshot(month: str, currency: str) -> dict:
    """Numbers fed to the insights generator for one month."""
    frm, to = _month_bounds(month)
    tot = total_spend(frm, to, currency)
    return {
        "month": month, "currency": currency, "total": tot["total"], "receipt_count": tot["receipt_count"],
        "per_category": category_breakdown(frm, to, currency),
        "per_merchant": merchant_breakdown(frm, to, currency, 5),
        "top_items": top_items(frm, to, currency, 5),
        "month_over_month": month_over_month(month, currency),
        "budget_status": budget_status(month, currency),
    }


def receipt_fingerprint(month: str, currency: str) -> str:
    """Hash of receipt ids + totals for cache invalidation."""
    import hashlib

    db.init()
    frm, to = _month_bounds(month)
    w, p = _where(frm, to, currency)
    with storage.get_connection() as conn:
        rows = conn.execute(f"SELECT r.id, r.total FROM receipts r{w} ORDER BY r.id", p).fetchall()
    budgets = sorted(db.budgets_map(currency).items())
    raw = "|".join(f"{r['id']}:{r['total']}" for r in rows) + repr(budgets)
    return hashlib.sha256(raw.encode()).hexdigest()
