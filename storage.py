"""
Single Source of Truth Storage Layer: SQLite Database.

Replaces legacy JSON flat file storage with SQLite database providing:
- Atomic ACID transactions.
- Relational integrity between Receipts and Line Items.
- Auto-migration from legacy receipts_db/receipts.json.
- Persistent User Corrections memory table for ML active learning.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "receipts_db", "receipts.db")
LEGACY_JSON_FILE = os.path.join(BASE_DIR, "receipts_db", "receipts.json")


def get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with foreign keys enabled."""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables and run automatic migration if needed."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                month TEXT NOT NULL,
                store TEXT NOT NULL,
                subtotal REAL DEFAULT 0.0,
                tax REAL DEFAULT 0.0,
                total REAL NOT NULL,
                currency TEXT DEFAULT '₹',
                payment_method TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS line_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                quantity REAL DEFAULT 1.0,
                unit_price REAL DEFAULT 0.0,
                confidence REAL DEFAULT 1.0,
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                category TEXT NOT NULL,
                store TEXT DEFAULT '',
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_receipts_month ON receipts(month);
            CREATE INDEX IF NOT EXISTS idx_line_items_receipt ON line_items(receipt_id);
            CREATE INDEX IF NOT EXISTS idx_corrections_norm ON user_corrections(normalized_text);
        """)

    # Check for legacy JSON migration
    _migrate_legacy_json_if_needed()


def _migrate_legacy_json_if_needed():
    """Migrate records from legacy receipts.json to SQLite if receipts table is empty."""
    if not os.path.exists(LEGACY_JSON_FILE):
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM receipts")
        if cur.fetchone()[0] > 0:
            return  # Already migrated

        try:
            with open(LEGACY_JSON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        if not isinstance(data, dict):
            return

        migrated_count = 0
        for month_key, receipts_list in data.items():
            for r in receipts_list:
                r_id = r.get("id") or f"REC-{month_key.replace('-', '')}-{migrated_count+1:03d}"
                r_date = r.get("date", f"{month_key}-01")
                cur.execute(
                    """
                    INSERT OR IGNORE INTO receipts 
                    (id, date, month, store, subtotal, tax, total, currency, payment_method, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r_id,
                        r_date,
                        month_key,
                        r.get("store", "Unknown Store"),
                        float(r.get("subtotal", 0.0)),
                        float(r.get("tax", 0.0)),
                        float(r.get("total", 0.0)),
                        r.get("currency", "₹"),
                        r.get("payment_method", ""),
                        r.get("notes", ""),
                        r.get("created_at", datetime.now().isoformat()),
                    ),
                )
                for item in r.get("items", []):
                    cur.execute(
                        """
                        INSERT INTO line_items
                        (receipt_id, item_name, category, amount, quantity, unit_price, confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r_id,
                            item.get("item_name", "Item"),
                            item.get("category", "Other"),
                            float(item.get("amount", 0.0)),
                            float(item.get("quantity", 1.0)),
                            float(item.get("unit_price", 0.0)),
                            float(item.get("confidence", 1.0)),
                        ),
                    )
                migrated_count += 1
        conn.commit()


def save_receipt(
    store_name: str,
    items: List[dict],
    scan_date: Optional[str] = None,
    grand_total: Optional[float] = None,
    currency: str = "₹",
    payment_method: str = "",
    notes: str = "",
    subtotal: float = 0.0,
    tax: float = 0.0,
    receipt_id: Optional[str] = None,
) -> str:
    """Save a receipt and its line items atomically to SQLite."""
    init_db()

    actual_date = scan_date.strip() if scan_date else datetime.now().strftime("%Y-%m-%d")
    try:
        month_key = datetime.strptime(actual_date, "%Y-%m-%d").strftime("%Y-%m")
    except ValueError:
        month_key = datetime.now().strftime("%Y-%m")

    calculated_total = round(sum(float(it.get("amount", 0.0)) for it in items), 2)
    final_total = round(grand_total, 2) if grand_total is not None else calculated_total

    with get_connection() as conn:
        cur = conn.cursor()
        if not receipt_id:
            cur.execute("SELECT COUNT(*) FROM receipts WHERE month = ?", (month_key,))
            seq = cur.fetchone()[0] + 1
            receipt_id = f"REC-{month_key.replace('-', '')}-{seq:03d}"

        cur.execute(
            """
            INSERT OR REPLACE INTO receipts
            (id, date, month, store, subtotal, tax, total, currency, payment_method, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                actual_date,
                month_key,
                store_name.strip() or "Unknown Store",
                subtotal,
                tax,
                final_total,
                currency,
                payment_method,
                notes,
                datetime.now().isoformat(),
            ),
        )

        # Delete existing line items if replacing
        cur.execute("DELETE FROM line_items WHERE receipt_id = ?", (receipt_id,))

        for item in items:
            cur.execute(
                """
                INSERT INTO line_items
                (receipt_id, item_name, category, amount, quantity, unit_price, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    str(item.get("item_name", "Item")).strip(),
                    str(item.get("category", "Other")).strip(),
                    float(item.get("amount", 0.0)),
                    float(item.get("quantity", 1.0)),
                    float(item.get("unit_price", abs(float(item.get("amount", 0.0))))),
                    float(item.get("confidence", 1.0)),
                ),
            )
        conn.commit()

    return receipt_id


def get_receipt(receipt_id: str) -> Optional[dict]:
    """Retrieve a full receipt dictionary with line items by ID."""
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,))
        row = cur.fetchone()
        if not row:
            return None

        receipt_dict = dict(row)
        cur.execute("SELECT * FROM line_items WHERE receipt_id = ?", (receipt_id,))
        items = [dict(r) for r in cur.fetchall()]
        receipt_dict["items"] = items
        return receipt_dict


def delete_receipt(receipt_id: str) -> bool:
    """Delete a receipt and its associated line items (via cascade)."""
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
        conn.commit()
        return cur.rowcount > 0


def list_receipts(month_key: Optional[str] = None, search_query: Optional[str] = None) -> List[dict]:
    """Query receipts with optional month filter and search string."""
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        query = "SELECT * FROM receipts"
        params: List[Any] = []
        conditions: List[str] = []

        if month_key:
            conditions.append("month = ?")
            params.append(month_key)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY date DESC, created_at DESC"
        cur.execute(query, params)
        receipt_rows = cur.fetchall()

        results = []
        q_lower = search_query.lower().strip() if search_query else ""

        for r_row in receipt_rows:
            r = dict(r_row)
            cur.execute("SELECT * FROM line_items WHERE receipt_id = ?", (r["id"],))
            items = [dict(it) for it in cur.fetchall()]
            r["items"] = items

            if q_lower:
                store_match = q_lower in r.get("store", "").lower()
                item_match = any(q_lower in it.get("item_name", "").lower() for it in items)
                if not (store_match or item_match):
                    continue

            results.append(r)

        return results


def get_available_months() -> List[str]:
    """Return distinct statement months sorted descending (newest first)."""
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT month FROM receipts ORDER BY month DESC")
        return [row[0] for row in cur.fetchall()]


def get_monthly_data(month_key: Optional[str] = None) -> Optional[dict]:
    """Compute comprehensive monthly aggregates and metrics directly from SQLite."""
    init_db()
    available = get_available_months()
    if not available:
        return None

    target_month = month_key if (month_key and month_key in available) else available[0]
    receipts = list_receipts(month_key=target_month)
    if not receipts:
        return None

    category_totals = defaultdict(float)
    category_counts = defaultdict(int)
    store_totals = defaultdict(float)
    store_counts = defaultdict(int)
    weekly = defaultdict(float)
    currencies = []

    for r in receipts:
        s = r.get("store", "Unknown Store")
        r_tot = float(r.get("total", 0.0))
        store_totals[s] += r_tot
        store_counts[s] += 1
        currencies.append(r.get("currency", "₹"))

        for item in r.get("items", []):
            cat = item.get("category", "Other")
            amt = float(item.get("amount", 0.0))
            category_totals[cat] += amt
            category_counts[cat] += 1

        d_str = r.get("date", "")
        try:
            day = int(d_str.split("-")[2][:2])
            w_label = f"Week {(day - 1) // 7 + 1}"
        except Exception:
            w_label = "Week 1"
        weekly[w_label] += r_tot

    dominant_curr = Counter(currencies).most_common(1)[0][0] if currencies else "₹"
    grand_total = sum(float(r.get("total", 0.0)) for r in receipts)
    avg_per_receipt = grand_total / len(receipts) if receipts else 0.0
    top_cat = max(category_totals, key=category_totals.get) if category_totals else "—"

    # MoM calculation
    all_months_asc = sorted(available)
    mom_change_pct = 0.0
    prev_month = None
    if target_month in all_months_asc:
        idx = all_months_asc.index(target_month)
        if idx > 0:
            prev_month = all_months_asc[idx - 1]
            prev_receipts = list_receipts(month_key=prev_month)
            prev_total = sum(float(r.get("total", 0.0)) for r in prev_receipts)
            if prev_total > 0:
                mom_change_pct = round(((grand_total - prev_total) / prev_total) * 100, 1)

    return {
        "month": target_month,
        "receipts": receipts,
        "receipt_count": len(receipts),
        "grand_total": round(grand_total, 2),
        "avg_per_receipt": round(avg_per_receipt, 2),
        "currency": dominant_curr,
        "category_totals": dict(category_totals),
        "category_counts": dict(category_counts),
        "store_totals": dict(store_totals),
        "store_counts": dict(store_counts),
        "weekly": dict(weekly),
        "top_category": top_cat,
        "mom_change_pct": mom_change_pct,
        "prev_month": prev_month,
    }


# ─── User Corrections & Personalization Memory (Active Learning) ─────────────
def record_user_correction(raw_text: str, category: str, store: str = ""):
    """Store user correction for personalized future classification."""
    init_db()
    norm = raw_text.strip().lower()
    if len(norm) < 2:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_corrections (raw_text, normalized_text, category, store, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (raw_text.strip(), norm, category.strip(), store.strip(), datetime.now().isoformat()),
        )
        conn.commit()


def get_user_correction(item_text: str) -> Optional[str]:
    """
    Check if user previously corrected this item text.
    1. Exact normalized match.
    2. Fallback to token-level high similarity match.
    """
    init_db()
    norm = item_text.strip().lower()
    if len(norm) < 2:
        return None

    with get_connection() as conn:
        cur = conn.cursor()
        # 1. Exact match (latest correction wins)
        cur.execute(
            "SELECT category FROM user_corrections WHERE normalized_text = ? ORDER BY id DESC LIMIT 1",
            (norm,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        # 2. Match when the known user-corrected phrase is a complete subphrase inside the query
        cur.execute("SELECT normalized_text, category FROM user_corrections ORDER BY id DESC")
        corrections = cur.fetchall()
        for corr_norm, cat in corrections:
            # e.g., if corr_norm is "basmati rice", it matches "organic basmati rice 5kg"
            if len(corr_norm) >= 4 and f" {corr_norm} " in f" {norm} ":
                return cat

    return None


def get_all_user_corrections() -> List[Tuple[str, str]]:
    """Retrieve all user corrections as (normalized_text, category) tuples."""
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT normalized_text, category FROM user_corrections ORDER BY id ASC")
        return cur.fetchall()


def count_user_corrections() -> int:
    """Return total number of saved user corrections."""
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM user_corrections")
        row = cur.fetchone()
        return row[0] if row else 0

