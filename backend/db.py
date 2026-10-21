"""v3 schema migrations, budgets, insights cache, file storage and duplicate lookup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import storage

SCHEMA_VERSION = 2

_RECEIPT_COLUMNS = {
    "merchant_address": "TEXT",
    "time": "TEXT",
    "invoice_number": "TEXT",
    "file_path": "TEXT",
    "file_hash": "TEXT",
    "engine_used": "TEXT",
    "reconciliation_status": "TEXT",
    "raw_text": "TEXT",
}


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    """Apply v1 -> v2 migration once (tracked with PRAGMA user_version). Never drops rows."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    existing = _columns(conn, "receipts")
    for col, typ in _RECEIPT_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE receipts ADD COLUMN {col} {typ}")
    if "discount" not in _columns(conn, "line_items"):
        conn.execute("ALTER TABLE line_items ADD COLUMN discount REAL DEFAULT 0")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_receipts_file_hash ON receipts(file_hash);
        CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(date);
        CREATE TABLE IF NOT EXISTS budgets (
            category TEXT PRIMARY KEY,
            monthly_limit REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR'
        );
        CREATE TABLE IF NOT EXISTS insights_cache (
            month TEXT NOT NULL,
            currency TEXT NOT NULL,
            data_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (month, currency)
        );
    """)
    # Legacy display symbols -> ISO codes
    for row in conn.execute("SELECT DISTINCT currency FROM receipts").fetchall():
        old = row[0]
        new = storage.to_iso_currency(old)
        if old != new:
            conn.execute("UPDATE receipts SET currency = ? WHERE currency IS ?", (new, old))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init() -> None:
    storage.init_db()


# ─── Budgets ────────────────────────────────────────────────────────────────
def list_budgets() -> List[dict]:
    init()
    with storage.get_connection() as conn:
        rows = conn.execute("SELECT category, monthly_limit, currency FROM budgets ORDER BY category").fetchall()
        return [dict(r) for r in rows]


def budgets_map(currency: str = "INR") -> Dict[str, float]:
    return {b["category"]: float(b["monthly_limit"]) for b in list_budgets() if b["currency"] == currency}


def upsert_budgets(items: List[dict]) -> None:
    init()
    with storage.get_connection() as conn:
        for b in items:
            conn.execute(
                "INSERT INTO budgets (category, monthly_limit, currency) VALUES (?, ?, ?) "
                "ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit, currency = excluded.currency",
                (b["category"], float(b["monthly_limit"]), storage.to_iso_currency(b.get("currency"))),
            )
        conn.commit()


def delete_budget(category: str) -> bool:
    init()
    with storage.get_connection() as conn:
        cur = conn.execute("DELETE FROM budgets WHERE category = ?", (category,))
        conn.commit()
        return cur.rowcount > 0


# ─── Insights cache ─────────────────────────────────────────────────────────
def get_cached_insights(month: str, currency: str, data_hash: str) -> Optional[dict]:
    init()
    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT data_hash, payload FROM insights_cache WHERE month = ? AND currency = ?", (month, currency)
        ).fetchone()
    if row and row["data_hash"] == data_hash:
        return json.loads(row["payload"])
    return None


def set_cached_insights(month: str, currency: str, data_hash: str, payload: dict) -> None:
    init()
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO insights_cache (month, currency, data_hash, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (month, currency, data_hash, json.dumps(payload), datetime.now().isoformat()),
        )
        conn.commit()


# ─── Files ──────────────────────────────────────────────────────────────────
def files_dir() -> str:
    path = os.path.join(os.path.dirname(storage.db_file()), "files")
    os.makedirs(path, exist_ok=True)
    return path


_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp|pdf)$")


def save_upload(data: bytes, ext: str) -> str:
    """Store upload content-addressed by sha256. Returns the upload_id (``<sha256>.<ext>``)."""
    digest = hashlib.sha256(data).hexdigest()
    upload_id = f"{digest}.{ext}"
    path = os.path.join(files_dir(), upload_id)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return upload_id


def resolve_upload(upload_id: Optional[str]) -> Optional[str]:
    """Return the absolute stored file path for a validated upload_id, else None."""
    if not upload_id or not _UPLOAD_ID_RE.match(upload_id):
        return None
    path = os.path.join(files_dir(), upload_id)
    return path if os.path.exists(path) else None


def normalize_merchant(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def find_duplicate(file_hash: Optional[str], merchant: str = "", date: str = "", total: Optional[float] = None,
                   exclude_id: Optional[str] = None) -> Optional[str]:
    """Return id of an existing receipt with the same file hash, or same merchant+date+total."""
    init()
    with storage.get_connection() as conn:
        if file_hash:
            row = conn.execute("SELECT id FROM receipts WHERE file_hash = ? AND id IS NOT ?",
                               (file_hash, exclude_id)).fetchone()
            if row:
                return row[0]
        if merchant and date and total is not None:
            norm = normalize_merchant(merchant)
            rows = conn.execute("SELECT id, store FROM receipts WHERE date = ? AND abs(total - ?) < 0.01 AND id IS NOT ?",
                                (date, float(total), exclude_id)).fetchall()
            for r in rows:
                if norm and normalize_merchant(r["store"]) == norm:
                    return r["id"]
    return None
