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

SCHEMA_VERSION = 5

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


_V3_SQL = """
    CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE COLLATE NOCASE, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS splits (id INTEGER PRIMARY KEY AUTOINCREMENT,
      receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE CASCADE,
      payer_id INTEGER NULL REFERENCES people(id),
      method TEXT NOT NULL CHECK (method IN ('equal','exact','percent','shares','items')),
      total_minor INTEGER NOT NULL, currency TEXT NOT NULL,
      your_share_minor INTEGER NOT NULL, you_lent_minor INTEGER NOT NULL DEFAULT 0,
      you_owe_minor INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS split_shares (id INTEGER PRIMARY KEY AUTOINCREMENT,
      split_id INTEGER NOT NULL REFERENCES splits(id) ON DELETE CASCADE,
      person_id INTEGER NULL REFERENCES people(id), share_minor INTEGER NOT NULL, input_value REAL NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_split_shares ON split_shares(split_id, IFNULL(person_id, 0));
    CREATE TABLE IF NOT EXISTS split_items (split_id INTEGER NOT NULL REFERENCES splits(id) ON DELETE CASCADE,
      item_index INTEGER NOT NULL, person_id INTEGER NULL REFERENCES people(id));
    CREATE UNIQUE INDEX IF NOT EXISTS ux_split_items ON split_items(split_id, item_index, IFNULL(person_id, 0));
    CREATE TABLE IF NOT EXISTS settlements (id INTEGER PRIMARY KEY AUTOINCREMENT,
      person_id INTEGER NOT NULL REFERENCES people(id),
      direction TEXT NOT NULL CHECK (direction IN ('you_paid','they_paid')),
      amount_minor INTEGER NOT NULL CHECK (amount_minor > 0), currency TEXT NOT NULL,
      date TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL);
"""


TXN_TYPES = ("expense", "income", "transfer_self", "transfer_person", "refund", "card_bill_payment",
             "cash_withdrawal", "investment")
COUNTED_TYPES = ("expense", "transfer_person", "cash_withdrawal")


def counted_predicate(alias: str = "t") -> str:
    """SQL condition for a statement transaction that counts as spending (unlinked spending debit)."""
    types = ",".join(f"'{t}'" for t in COUNTED_TYPES)
    return (f"{alias}.direction = 'debit' AND {alias}.excluded = 0 AND {alias}.matched_receipt_id IS NULL "
            f"AND {alias}.txn_type IN ({types})")


_TYPES_SQL = ",".join(f"'{t}'" for t in TXN_TYPES)
_V4_SQL = f"""
    CREATE TABLE IF NOT EXISTS statements (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,
      file_hash TEXT NOT NULL, format TEXT NOT NULL CHECK (format IN ('csv','xlsx','xls','pdf')),
      source TEXT NOT NULL DEFAULT 'generic', account_label TEXT NOT NULL DEFAULT '', account_hint TEXT,
      account_key TEXT NOT NULL, period_start TEXT, period_end TEXT, currency TEXT NOT NULL DEFAULT 'INR',
      rows_parsed INTEGER NOT NULL DEFAULT 0, rows_new INTEGER NOT NULL DEFAULT 0,
      rows_duplicate INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
      statement_id INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
      account_key TEXT NOT NULL, date TEXT NOT NULL, month TEXT NOT NULL, description TEXT NOT NULL,
      amount REAL NOT NULL CHECK (amount > 0), direction TEXT NOT NULL CHECK (direction IN ('debit','credit')),
      currency TEXT NOT NULL DEFAULT 'INR', balance REAL, reference TEXT, counterparty TEXT, vpa TEXT,
      merchant TEXT, payee_key TEXT, channel TEXT,
      txn_type TEXT NOT NULL CHECK (txn_type IN ({_TYPES_SQL})),
      category TEXT NOT NULL DEFAULT 'Other',
      category_source TEXT NOT NULL DEFAULT 'none' CHECK (category_source IN ('rule','memory','ai','user','none')),
      confidence REAL NOT NULL DEFAULT 0, needs_review INTEGER NOT NULL DEFAULT 0,
      excluded INTEGER NOT NULL DEFAULT 0,
      matched_receipt_id TEXT REFERENCES receipts(id) ON DELETE SET NULL,
      match_source TEXT CHECK (match_source IN ('auto','user')), match_score REAL,
      dedupe_key TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_dedupe ON transactions(dedupe_key);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_receipt ON transactions(matched_receipt_id)
      WHERE matched_receipt_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
    CREATE INDEX IF NOT EXISTS idx_txn_payee ON transactions(payee_key);
    CREATE TABLE IF NOT EXISTS match_candidates (
      transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
      receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE, score REAL NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending','rejected')), PRIMARY KEY (transaction_id, receipt_id));
    CREATE TABLE IF NOT EXISTS payee_rules (payee_key TEXT PRIMARY KEY, category TEXT NOT NULL, txn_type TEXT,
      updated_at TEXT NOT NULL);
    CREATE VIEW IF NOT EXISTS spend_docs AS
      SELECT r.id doc_id, 'receipt' source, r.date, r.month, r.currency, r.store merchant,
             COALESCE(r.my_share, r.total) amount FROM receipts r
      UNION ALL SELECT 'txn:' || t.id, 'statement', t.date, t.month, t.currency,
             COALESCE(t.merchant, t.counterparty, t.description), t.amount FROM transactions t
      WHERE {counted_predicate()};
    CREATE VIEW IF NOT EXISTS spend_lines AS
      SELECT r.id doc_id, 'receipt' source, r.date, r.currency, r.store merchant, li.category, li.item_name,
             li.amount * li.share_weight amount, li.share_weight FROM line_items li JOIN receipts r ON r.id = li.receipt_id
      UNION ALL SELECT 'txn:' || t.id, 'statement', t.date, t.currency,
             COALESCE(t.merchant, t.counterparty, t.description), t.category, COALESCE(t.merchant, t.description),
             t.amount, 1.0 FROM transactions t WHERE {counted_predicate()};
"""

# A refund credit counts as negative spending in the category of the payee's latest purchase.
_REFUND_WHERE = "t.direction = 'credit' AND t.txn_type = 'refund' AND t.excluded = 0"
_REFUND_CAT = ("COALESCE((SELECT e.category FROM transactions e WHERE e.payee_key = t.payee_key "
               "AND e.direction = 'debit' AND e.txn_type = 'expense' AND e.date <= t.date "
               "ORDER BY e.date DESC LIMIT 1), 'Other')")
_V5_VIEWS = f"""
    CREATE VIEW spend_docs AS
      SELECT r.id doc_id, 'receipt' source, r.date, r.month, r.currency, r.store merchant,
             COALESCE(r.my_share, r.total) amount FROM receipts r
      UNION ALL SELECT 'txn:' || t.id, 'statement', t.date, t.month, t.currency,
             COALESCE(t.merchant, t.counterparty, t.description), t.amount FROM transactions t
      WHERE {counted_predicate()}
      UNION ALL SELECT 'txn:' || t.id, 'refund', t.date, t.month, t.currency,
             COALESCE(t.merchant, t.counterparty, t.description), -t.amount FROM transactions t
      WHERE {_REFUND_WHERE};
    CREATE VIEW spend_lines AS
      SELECT r.id doc_id, 'receipt' source, r.date, r.currency, r.store merchant, li.category, li.item_name,
             li.amount * li.share_weight amount, li.share_weight FROM line_items li JOIN receipts r ON r.id = li.receipt_id
      UNION ALL SELECT 'txn:' || t.id, 'statement', t.date, t.currency,
             COALESCE(t.merchant, t.counterparty, t.description), t.category, COALESCE(t.merchant, t.description),
             t.amount, 1.0 FROM transactions t WHERE {counted_predicate()}
      UNION ALL SELECT 'txn:' || t.id, 'refund', t.date, t.currency,
             COALESCE(t.merchant, t.counterparty, t.description), {_REFUND_CAT},
             'Refund: ' || COALESCE(t.merchant, t.description), -t.amount, 1.0 FROM transactions t
      WHERE {_REFUND_WHERE};
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations once (tracked with PRAGMA user_version). Never drops rows."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version < 2:
        _migrate_v2(conn)
    if version < 3:
        if "my_share" not in _columns(conn, "receipts"):
            conn.execute("ALTER TABLE receipts ADD COLUMN my_share REAL")
        if "share_weight" not in _columns(conn, "line_items"):
            conn.execute("ALTER TABLE line_items ADD COLUMN share_weight REAL NOT NULL DEFAULT 1.0")
        conn.executescript(_V3_SQL)
    if version < 4:
        conn.executescript(_V4_SQL)
    if version < 5:
        # Views only (no data change): refunds now reduce spending instead of being ignored.
        conn.executescript("DROP VIEW IF EXISTS spend_docs; DROP VIEW IF EXISTS spend_lines;" + _V5_VIEWS)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _migrate_v2(conn: sqlite3.Connection) -> None:
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
