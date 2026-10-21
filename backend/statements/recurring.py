"""Recurring payment detection (subscriptions, bills): same payee, regular interval, stable amount."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date as date_cls
from datetime import timedelta
from typing import List

import storage
from backend import db


def detect(rows: List[dict]) -> List[dict]:
    """rows: dicts with payee_key, merchant, date, amount, currency, category (debits only)."""
    groups = defaultdict(list)
    for r in rows:
        if r.get("payee_key"):
            groups[(r["payee_key"], r.get("currency") or "INR")].append(r)
    out = []
    for (_, cur), items in groups.items():
        if len(items) < 3:
            continue
        items.sort(key=lambda r: r["date"])
        dates = [date_cls.fromisoformat(r["date"]) for r in items]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        med = statistics.median(gaps)
        if 25 <= med <= 35:
            cadence = "monthly"
        elif 6 <= med <= 8:
            cadence = "weekly"
        else:
            continue
        tol = 5 if cadence == "monthly" else 1
        amounts = [r["amount"] for r in items]
        if any(abs(g - med) > tol for g in gaps) or max(amounts) / min(amounts) > 1.15:
            continue
        last = items[-1]
        out.append({"payee_key": last["payee_key"], "merchant": last.get("merchant") or last["payee_key"],
                    "category": last.get("category"), "cadence": cadence, "count": len(items),
                    "average_amount": round(sum(amounts) / len(amounts), 2), "last_amount": last["amount"],
                    "last_date": last["date"], "next_expected": (dates[-1] + timedelta(days=round(med))).isoformat(),
                    "currency": cur, "monthly_cost": round(sum(amounts) / len(amounts) * (1 if cadence == "monthly"
                                                                                           else 52 / 12), 2)})
    return sorted(out, key=lambda r: -r["monthly_cost"])


def recurring_payments() -> List[dict]:
    db.init()
    with storage.get_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT payee_key, merchant, date, amount, currency, category FROM transactions WHERE direction = 'debit' "
            "AND excluded = 0 AND txn_type IN ('expense','transfer_person') ORDER BY date")]
    return detect(rows)
