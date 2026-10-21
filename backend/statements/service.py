"""Two-phase statement import: preview (parse + cache, nothing stored) -> commit (insert new rows, match receipts)."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional

import storage
from backend import config, db
from backend.statements import categorize, dedupe, matching, parsing
from backend.statements.narration import display_merchant, parse_narration, payee_key
from backend.statements.parsing import StatementError

_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_LOCK = threading.Lock()
_CACHE_MAX = 8


class PreviewExpired(StatementError):
    def __init__(self):
        super().__init__("This preview expired. Upload the statement again.", status=410)


def _put(token: str, entry: dict) -> None:
    with _LOCK:
        _CACHE[token] = entry
        _CACHE.move_to_end(token)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _get(token: str) -> dict:
    with _LOCK:
        e = _CACHE.get(token)
        if not e or time.time() - e["created"] > config.PREVIEW_TTL_S:
            _CACHE.pop(token, None)
            raise PreviewExpired()
        return e


def _read(data: bytes, password: Optional[str]) -> tuple:
    fmt = parsing.sniff(data)
    if fmt == "pdf":
        from backend.statements.pdf_text import read_pdf
        rows, text = read_pdf(data, password)
    else:
        rows = {"csv": parsing.read_csv, "xlsx": parsing.read_xlsx, "xls": parsing.read_xls}[fmt](data)
        text = "\n".join(" ".join(r) for r in rows[:30])
    if not rows:
        raise StatementError("No rows found in this statement.")
    return fmt, rows, text


def preview(data: bytes, filename: str, password: Optional[str] = None, account_label: str = "") -> dict:
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise StatementError("File is too large (max 15 MB).", status=413)
    fmt, rows, text = _read(data, password)
    source, hint = parsing.detect_source(text)
    mapping, conf = parsing.detect_mapping(rows)
    token = hashlib.sha256(data).hexdigest()
    entry = {"created": time.time(), "filename": (filename or "statement")[:200], "format": fmt, "rows": rows,
             "source": source, "hint": hint, "file_hash": token, "account_label": account_label[:60],
             "mapping": mapping if mapping and conf >= 0.8 else None, "guess": mapping}
    _put(token, entry)
    return _preview(token, entry)


def reparse(token: str, mapping: dict) -> dict:
    e = _get(token)
    ncols = max((len(r) for r in e["rows"]), default=0)
    parsing.validate_mapping(mapping, ncols, len(e["rows"]))
    e["mapping"] = mapping
    return _preview(token, e)


def _enrich(txns: List[dict], acct: str, currency: str = "INR") -> None:
    for t in txns:
        n = parse_narration(t["description"])
        t.update(channel=n.channel, counterparty=n.counterparty, vpa=n.vpa, utr=n.utr, is_person=n.is_person,
                 payee_key=payee_key(n, t["description"]), merchant=display_merchant(n, t["description"]),
                 currency=currency, month=t["date"][:7])
    dedupe.assign_keys(txns, acct)


def _build(e: dict, label: Optional[str] = None) -> tuple:
    m = e["mapping"]
    txns, skipped = parsing.apply_mapping(e["rows"], m)
    acct = dedupe.account_key(e["source"], e["hint"], label if label is not None else e["account_label"])
    _enrich(txns, acct)
    return txns, skipped, acct


def _existing_keys(keys: List[str]) -> set:
    db.init()
    out: set = set()
    with storage.get_connection() as conn:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = f"SELECT dedupe_key FROM transactions WHERE dedupe_key IN ({','.join('?' * len(chunk))})"
            out.update(r[0] for r in conn.execute(q, chunk))
    return out


def _memory() -> Dict[str, dict]:
    db.init()
    with storage.get_connection() as conn:
        return {r["payee_key"]: dict(r) for r in conn.execute("SELECT payee_key, category, txn_type FROM payee_rules")}


def _public_txn(t: dict) -> dict:
    keys = ("date", "description", "amount", "direction", "balance", "reference", "merchant", "counterparty",
            "txn_type", "category", "needs_review")
    return {k: t.get(k) for k in keys}


def _preview(token: str, e: dict) -> dict:
    rows = e["rows"]
    base = {"token": token, "filename": e["filename"], "format": e["format"], "source": e["source"],
            "account_hint": e["hint"], "columns": [], "sample_rows": rows[:15], "mapping": e["mapping"] or e["guess"],
            "needs_mapping": e["mapping"] is None, "rows_parsed": 0, "rows_skipped": 0, "likely_duplicates": 0,
            "likely_new": 0, "period_start": None, "period_end": None, "transactions": []}
    with storage.get_connection() as conn:
        db.init()
        base["already_imported"] = bool(conn.execute("SELECT 1 FROM statements WHERE file_hash = ?",
                                                     (e["file_hash"],)).fetchone())
    m = e["mapping"] or e["guess"]
    if m and 0 <= m.get("header_row", -1) < len(rows):
        base["columns"] = rows[m["header_row"]]
    else:
        base["columns"] = [f"Column {i + 1}" for i in range(max((len(r) for r in rows[:40]), default=0))]
    if e["mapping"] is None:
        return base
    txns, skipped, _ = _build(e)
    if not txns:
        base["needs_mapping"] = True
        return base
    categorize.categorize(txns, _memory(), use_ai=False)
    existing = _existing_keys([t["dedupe_key"] for t in txns])
    dup = sum(1 for t in txns if t["dedupe_key"] in existing)
    dates = sorted(t["date"] for t in txns)
    base.update(rows_parsed=len(txns), rows_skipped=skipped, likely_duplicates=dup, likely_new=len(txns) - dup,
                period_start=dates[0], period_end=dates[-1], transactions=[_public_txn(t) for t in txns[:50]])
    return base


_COLS = ("statement_id", "account_key", "date", "month", "description", "amount", "direction", "currency", "balance",
         "reference", "counterparty", "vpa", "merchant", "payee_key", "channel", "txn_type", "category",
         "category_source", "confidence", "needs_review", "dedupe_key", "created_at")


def commit(token: str, mapping: Optional[dict] = None, account_label: Optional[str] = None) -> dict:
    e = _get(token)
    if mapping is not None:
        reparse(token, mapping)
    if e["mapping"] is None:
        raise StatementError("Map the statement columns before importing.", status=422)
    txns, skipped, acct = _build(e, account_label)
    if not txns:
        raise StatementError("No transactions found with this column mapping.", status=422)
    existing = _existing_keys([t["dedupe_key"] for t in txns])
    seen_in_file: set = set()
    new = []
    for t in txns:
        if t["dedupe_key"] in existing or t["dedupe_key"] in seen_in_file:
            continue
        seen_in_file.add(t["dedupe_key"])
        new.append(t)
    categorize.categorize(new, _memory(), use_ai=True)
    summary = {"statement_id": None, "rows_parsed": len(txns), "new": 0, "duplicates_skipped": len(txns) - len(new),
               "auto_categorized": 0, "needs_review": 0, "matched_receipts": 0, "suggested_matches": 0,
               "rows_skipped": skipped}
    if not new:
        return summary
    now = datetime.now().isoformat()
    dates = sorted(t["date"] for t in txns)
    new_ids: List[int] = []
    with storage.get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO statements (filename, file_hash, format, source, account_label, account_hint, account_key, "
            "period_start, period_end, currency, rows_parsed, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["filename"], e["file_hash"], e["format"], e["source"],
             (account_label if account_label is not None else e["account_label"])[:60], e["hint"], acct,
             dates[0], dates[-1], "INR", len(txns), now))
        sid = cur.lastrowid
        for t in new:
            t.update(statement_id=sid, account_key=acct, created_at=now)
            c = conn.execute(f"INSERT OR IGNORE INTO transactions ({', '.join(_COLS)}) "
                             f"VALUES ({', '.join('?' * len(_COLS))})", tuple(t.get(k) for k in _COLS))
            if c.rowcount:
                new_ids.append(c.lastrowid)
        if not new_ids:
            conn.rollback()
            return summary
        conn.execute("UPDATE statements SET rows_new = ?, rows_duplicate = ? WHERE id = ?",
                     (len(new_ids), len(txns) - len(new_ids), sid))
        conn.commit()
    linked, suggested = matching.match_transactions(new_ids)
    summary.update(statement_id=sid, new=len(new_ids), duplicates_skipped=len(txns) - len(new_ids),
                   auto_categorized=sum(1 for t in new if t["category_source"] != "none" and not t["needs_review"]),
                   needs_review=sum(1 for t in new if t["needs_review"]), matched_receipts=linked,
                   suggested_matches=suggested)
    return summary


def list_statements() -> List[dict]:
    db.init()
    with storage.get_connection() as conn:
        rows = conn.execute("SELECT s.*, (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = s.id) txn_count "
                            "FROM statements s ORDER BY s.imported_at DESC, s.id DESC").fetchall()
    return [dict(r) for r in rows]


def delete_statement(statement_id: int) -> bool:
    db.init()
    with storage.get_connection() as conn:
        cur = conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
        conn.commit()
        return cur.rowcount > 0
