"""Link bank transactions to scanned receipts so one purchase is counted once (the receipt wins)."""

from __future__ import annotations

import difflib
import re
from datetime import date as date_cls
from typing import Iterable, List, Optional, Tuple

import storage
from backend import db
from statement_matcher import normalize_merchant, token_jaccard_similarity

AUTO_LINK = 0.85
SUGGEST = 0.6
MARGIN = 0.1
DATE_WINDOW = 3
_MATCHABLE = "t.direction = 'debit' AND t.excluded = 0 AND t.txn_type IN ('expense','transfer_person')"


class MatchError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_merchant(s or ""))


def merchant_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = _squash(a), _squash(b)
    if not sa or not sb:
        return 0.0
    scores = [token_jaccard_similarity(a, b), difflib.SequenceMatcher(None, sa, sb).ratio()]
    short, long_ = sorted((sa, sb), key=len)
    if len(short) >= 4 and short in long_:
        scores.append(0.8)
    s = max(scores)
    return s if s >= 0.2 else 0.0


def _txn_name(t: dict) -> str:
    return " ".join(filter(None, [t.get("merchant"), t.get("counterparty")])) or t.get("description", "")


def score(t: dict, r: dict) -> Optional[float]:
    if (t.get("currency") or "INR") != (r.get("currency") or "INR") or not r.get("total"):
        return None
    try:
        days = abs((date_cls.fromisoformat(t["date"]) - date_cls.fromisoformat(r["date"])).days)
    except (TypeError, ValueError):
        return None
    if days > DATE_WINDOW:
        return None
    m = max(merchant_similarity(_txn_name(t), r.get("store", "")),
            merchant_similarity(t.get("description", ""), r.get("store", "")))
    diff = t["amount"] - r["total"]
    if abs(diff) < 0.005:
        a = 1.0
    elif abs(diff) <= 1.0:
        a = 0.9
    elif 0 < diff <= 0.2 * r["total"] and m >= 0.5:
        a = 0.6  # tip added on the card
    else:
        return None
    return round(0.5 * a + 0.25 * (1 - days / (2 * DATE_WINDOW)) + 0.25 * m, 3)


def _receipts(conn, ids: Optional[Iterable[str]] = None) -> List[dict]:
    q = ("SELECT r.id, r.date, r.store, r.total, r.currency FROM receipts r WHERE NOT EXISTS "
         "(SELECT 1 FROM transactions t WHERE t.matched_receipt_id = r.id)")
    params: list = []
    if ids is not None:
        ids = list(ids)
        q += f" AND r.id IN ({','.join('?' * len(ids))})"
        params = ids
    return [dict(r) for r in conn.execute(q, params)]


def _txns(conn, ids: Optional[Iterable[int]] = None) -> List[dict]:
    q = f"SELECT * FROM transactions t WHERE {_MATCHABLE} AND t.matched_receipt_id IS NULL"
    params: list = []
    if ids is not None:
        ids = list(ids)
        q += f" AND t.id IN ({','.join('?' * len(ids))})"
        params = ids
    return [dict(r) for r in conn.execute(q, params)]


def _run(conn, txns: List[dict], receipts: List[dict]) -> Tuple[int, int]:
    rejected = {(r[0], r[1]) for r in conn.execute(
        "SELECT transaction_id, receipt_id FROM match_candidates WHERE status = 'rejected'")}
    pairs = []
    for t in txns:
        for r in receipts:
            if (t["id"], r["id"]) in rejected:
                continue
            s = score(t, r)
            if s is not None and s >= SUGGEST:
                pairs.append((s, t["id"], r["id"]))
    pairs.sort(key=lambda p: -p[0])
    used_t, used_r, linked = set(), set(), 0
    for s, tid, rid in pairs:
        if tid in used_t or rid in used_r or s < AUTO_LINK:
            continue
        runner = max([p[0] for p in pairs if (p[1] == tid) != (p[2] == rid) and (p[1] == tid or p[2] == rid)],
                     default=0.0)
        if runner >= s - MARGIN:
            continue
        conn.execute("UPDATE transactions SET matched_receipt_id = ?, match_source = 'auto', match_score = ? "
                     "WHERE id = ? AND matched_receipt_id IS NULL", (rid, s, tid))
        conn.execute("DELETE FROM match_candidates WHERE status = 'pending' AND (transaction_id = ? OR receipt_id = ?)",
                     (tid, rid))
        used_t.add(tid)
        used_r.add(rid)
        linked += 1
    suggested = 0
    for s, tid, rid in pairs:
        if tid in used_t or rid in used_r:
            continue
        c = conn.execute("INSERT OR IGNORE INTO match_candidates (transaction_id, receipt_id, score, status) "
                         "VALUES (?, ?, ?, 'pending')", (tid, rid, s))
        suggested += c.rowcount
    conn.commit()
    return linked, suggested


def match_transactions(txn_ids: List[int]) -> Tuple[int, int]:
    if not txn_ids:
        return 0, 0
    db.init()
    with storage.get_connection() as conn:
        return _run(conn, _txns(conn, txn_ids), _receipts(conn))


def match_receipt(receipt_id: str) -> dict:
    db.init()
    with storage.get_connection() as conn:
        rec = _receipts(conn, [receipt_id])
        if rec:
            near = [t for t in _txns(conn) if score(t, rec[0]) is not None]
            if near:  # other unlinked receipts compete too, so an ambiguous pair is suggested, not linked
                _run(conn, near, _receipts(conn))
    return receipt_bank_match(receipt_id)


def _brief(t: Optional[dict]) -> Optional[dict]:
    if not t:
        return None
    keys = ("id", "date", "description", "amount", "direction", "currency", "merchant", "category", "txn_type",
            "match_source", "match_score")
    return {k: t.get(k) for k in keys}


def receipt_bank_match(receipt_id: str) -> dict:
    db.init()
    with storage.get_connection() as conn:
        linked = conn.execute("SELECT * FROM transactions WHERE matched_receipt_id = ?", (receipt_id,)).fetchone()
        sugg = conn.execute("SELECT t.*, c.score cand_score FROM match_candidates c JOIN transactions t "
                            "ON t.id = c.transaction_id WHERE c.receipt_id = ? AND c.status = 'pending' "
                            "AND t.matched_receipt_id IS NULL ORDER BY c.score DESC", (receipt_id,)).fetchall()
    return {"linked": _brief(dict(linked)) if linked else None,
            "suggestions": [] if linked else [{"transaction": _brief(dict(s)), "score": s["cand_score"]} for s in sugg]}


def link(txn_id: int, receipt_id: str, source: str = "user") -> dict:
    db.init()
    with storage.get_connection() as conn:
        t = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if not t:
            raise MatchError("Transaction not found", 404)
        r = conn.execute("SELECT id, date, store, total, currency FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
        if not r:
            raise MatchError("Receipt not found", 404)
        if t["direction"] != "debit":
            raise MatchError("Only payments (debits) can be linked to a receipt.")
        if t["matched_receipt_id"]:
            raise MatchError("This transaction is already linked to a receipt.", 409)
        if conn.execute("SELECT 1 FROM transactions WHERE matched_receipt_id = ?", (receipt_id,)).fetchone():
            raise MatchError("This receipt is already linked to another transaction.", 409)
        s = score(dict(t), dict(r))
        conn.execute("UPDATE transactions SET matched_receipt_id = ?, match_source = ?, match_score = ?, "
                     "needs_review = 0 WHERE id = ?", (receipt_id, source, s, txn_id))
        conn.execute("DELETE FROM match_candidates WHERE status = 'pending' AND (transaction_id = ? OR receipt_id = ?)",
                     (txn_id, receipt_id))
        conn.commit()
    return get_txn(txn_id)


def unlink(txn_id: int) -> dict:
    db.init()
    with storage.get_connection() as conn:
        t = conn.execute("SELECT matched_receipt_id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if not t:
            raise MatchError("Transaction not found", 404)
        if not t[0]:
            raise MatchError("This transaction is not linked.", 409)
        conn.execute("UPDATE transactions SET matched_receipt_id = NULL, match_source = NULL, match_score = NULL "
                     "WHERE id = ?", (txn_id,))
        # Remember the user's decision so automatic matching doesn't re-link the pair
        conn.execute("INSERT INTO match_candidates (transaction_id, receipt_id, score, status) VALUES (?, ?, 0, "
                     "'rejected') ON CONFLICT(transaction_id, receipt_id) DO UPDATE SET status = 'rejected'",
                     (txn_id, t[0]))
        conn.commit()
    return get_txn(txn_id)


def reject(txn_id: int, receipt_id: str) -> None:
    db.init()
    with storage.get_connection() as conn:
        if not conn.execute("SELECT 1 FROM transactions WHERE id = ?", (txn_id,)).fetchone():
            raise MatchError("Transaction not found", 404)
        if not conn.execute("SELECT 1 FROM receipts WHERE id = ?", (receipt_id,)).fetchone():
            raise MatchError("Receipt not found", 404)
        conn.execute("INSERT INTO match_candidates (transaction_id, receipt_id, score, status) VALUES (?, ?, 0, "
                     "'rejected') ON CONFLICT(transaction_id, receipt_id) DO UPDATE SET status = 'rejected'",
                     (txn_id, receipt_id))
        conn.commit()


def get_txn(txn_id: int) -> Optional[dict]:
    db.init()
    with storage.get_connection() as conn:
        t = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return dict(t) if t else None


def receipt_candidates(txn_id: int) -> List[dict]:
    """Unlinked receipts within ±7 days and 25% of the amount (for the manual link picker)."""
    t = get_txn(txn_id)
    if not t:
        raise MatchError("Transaction not found", 404)
    with storage.get_connection() as conn:
        rows = conn.execute(
            "SELECT r.id, r.date, r.store, r.total, r.currency FROM receipts r WHERE abs(julianday(r.date) - "
            "julianday(?)) <= 7 AND r.total BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM transactions x "
            "WHERE x.matched_receipt_id = r.id) ORDER BY abs(r.total - ?) LIMIT 20",
            (t["date"], t["amount"] * 0.75, t["amount"] * 1.25, t["amount"])).fetchall()
    return [{**dict(r), "score": score(t, dict(r))} for r in rows]


def txn_candidates(receipt_id: str) -> List[dict]:
    """Unlinked debits within ±7 days and 25% of the receipt total."""
    rec = storage.get_receipt(receipt_id)
    if not rec:
        raise MatchError("Receipt not found", 404)
    with storage.get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM transactions t WHERE {_MATCHABLE} AND t.matched_receipt_id IS NULL AND "
            "abs(julianday(t.date) - julianday(?)) <= 7 AND t.amount BETWEEN ? AND ? ORDER BY abs(t.amount - ?) "
            "LIMIT 20", (rec["date"], rec["total"] * 0.75, rec["total"] * 1.25, rec["total"])).fetchall()
    return [{"transaction": _brief(dict(t)), "score": score(dict(t), rec)} for t in rows]
