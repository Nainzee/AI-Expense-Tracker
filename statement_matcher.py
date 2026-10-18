"""
Bank and UPI Statement Reconciliation Engine.

Matches scanned receipts against bank or UPI statement CSV exports using:
1. Multi-factor score: Amount agreement, Date window proximity (±3 days), and Fuzzy Merchant name similarity.
2. Classification into Verified Match (Score >= 0.80), Probable Match (0.60 <= Score < 0.80), or Unmatched.
3. Precision / Recall evaluation benchmark.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass
class BankTransaction:
    txn_id: str
    date: str              # YYYY-MM-DD
    description: str       # e.g. "UPI/SUPERMART RETAIL/294029/Payment"
    amount: float          # Debit amount
    txn_type: str = "DEBIT"
    reference: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReconciliationMatch:
    receipt_id: str
    receipt_store: str
    receipt_date: str
    receipt_amount: float
    txn_id: str
    txn_description: str
    txn_date: str
    txn_amount: float
    confidence_score: float
    match_status: str       # 'Verified Match', 'Probable Match', 'Unmatched'
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_merchant(name: str) -> str:
    """Strip common payment gateways and punctuation for fuzzy matching."""
    t = name.lower()
    t = re.sub(r"\b(?:upi|pos|imps|neft|ach|dr|cr|retail|store|ltd|pvt|co|supermarket)\b", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def token_jaccard_similarity(s1: str, s2: str) -> float:
    """Token overlap similarity."""
    t1 = set(normalize_merchant(s1).split())
    t2 = set(normalize_merchant(s2).split())
    if not t1 or not t2:
        return 0.0
    return len(t1.intersection(t2)) / len(t1.union(t2))


def parse_date_flexible(d_str: str) -> Optional[datetime]:
    """Parse various common date formats from statement CSVs."""
    formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%Y/%m/%d"
    ]
    cleaned = d_str.strip()
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            pass
    return None


def parse_bank_statement_csv(csv_content_or_path: str) -> List[BankTransaction]:
    """
    Parse a Bank / UPI statement CSV into structured BankTransaction objects.
    Autodetects column headers across standard Indian and global banks (HDFC, SBI, ICICI, Chase, etc.).
    """
    if os.path.exists(csv_content_or_path):
        with open(csv_content_or_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    else:
        lines = csv_content_or_path.strip().splitlines()

    reader = csv.reader(lines)
    header_row = None
    rows = []

    for r in reader:
        if not r or all(c.strip() == "" for c in r):
            continue
        lower_cols = [c.lower().strip() for c in r]
        # Look for header line containing date and amount/debit
        if any("date" in c for c in lower_cols) and any(x in " ".join(lower_cols) for x in ["amount", "debit", "withdrawal"]):
            header_row = lower_cols
            continue
        if header_row:
            rows.append(r)

    if not header_row:
        # Fallback default indices if no header detected
        date_idx, desc_idx, amt_idx = 0, 1, 2
    else:
        date_idx = next((i for i, c in enumerate(header_row) if "date" in c), 0)
        desc_idx = next((i for i, c in enumerate(header_row) if any(x in c for x in ["desc", "particular", "narration", "merchant", "details"])), 1)
        amt_idx = next((i for i, c in enumerate(header_row) if any(x in c for x in ["debit", "withdrawal", "amount"])), 2)

    transactions = []
    for idx, r in enumerate(rows):
        if len(r) <= max(date_idx, desc_idx, amt_idx):
            continue

        raw_date = r[date_idx].strip()
        dt = parse_date_flexible(raw_date)
        date_iso = dt.strftime("%Y-%m-%d") if dt else datetime.now().strftime("%Y-%m-%d")

        raw_desc = r[desc_idx].strip()
        raw_amt_str = re.sub(r"[^\d.-]", "", r[amt_idx].strip())

        try:
            amt = float(raw_amt_str)
        except ValueError:
            continue

        if amt > 0:
            transactions.append(BankTransaction(
                txn_id=f"TXN-{idx+1:04d}",
                date=date_iso,
                description=raw_desc,
                amount=round(amt, 2)
            ))

    return transactions


def compute_match_score(
    receipt_amt: float,
    receipt_date_str: str,
    receipt_store: str,
    txn: BankTransaction,
    days_window: int = 3,
    amt_tolerance: float = 1.0
) -> Tuple[float, str]:
    """
    Multi-factor reconciliation score:
    - Amount Score (50% weight): Exact match = 1.0, decay within tolerance.
    - Proximity Score (25% weight): Receipt date within ±3 days of settlement.
    - Merchant Score (25% weight): Token overlap / substring presence.
    """
    # 1. Amount Score
    amt_diff = abs(receipt_amt - txn.amount)
    if amt_diff <= 0.05:
        score_amt = 1.0
    elif amt_diff <= amt_tolerance:
        score_amt = max(0.0, 1.0 - (amt_diff / amt_tolerance) * 0.5)
    else:
        return 0.0, "Amount mismatch"

    # 2. Date Proximity Score
    d_rec = parse_date_flexible(receipt_date_str)
    d_txn = parse_date_flexible(txn.date)
    if d_rec and d_txn:
        day_diff = abs((d_txn - d_rec).days)
        if day_diff > days_window:
            return 0.0, f"Date out of ±{days_window}d window ({day_diff} days difference)"
        score_date = max(0.2, 1.0 - (day_diff / max(days_window, 1)) * 0.5)
    else:
        score_date = 0.5

    # 3. Merchant Similarity Score
    sim = token_jaccard_similarity(receipt_store, txn.description)
    norm_rec = normalize_merchant(receipt_store)
    norm_txn = normalize_merchant(txn.description)

    if norm_rec and norm_rec in norm_txn:
        score_merchant = 1.0
    elif sim > 0:
        score_merchant = sim
    else:
        score_merchant = 0.2  # Generic description fallback

    total_score = round(0.50 * score_amt + 0.25 * score_date + 0.25 * score_merchant, 3)
    explanation = f"Amt diff: {amt_diff:.2f}, Date diff: {day_diff if (d_rec and d_txn) else '?'}d, Merchant match: {score_merchant:.2f}"
    return total_score, explanation


def match_receipts_to_statement(
    receipts: List[dict],
    transactions: List[BankTransaction],
    days_window: int = 3,
    amt_tolerance: float = 1.0
) -> List[ReconciliationMatch]:
    """
    Reconcile a list of scanned receipts against bank transactions.
    """
    matches = []
    used_txns = set()

    for r in receipts:
        r_id = r.get("id", "REC-001")
        r_store = r.get("store", "Store")
        r_date = r.get("date", datetime.now().strftime("%Y-%m-%d"))
        r_amt = float(r.get("total", 0.0))

        best_score = 0.0
        best_txn = None
        best_expl = ""

        for txn in transactions:
            if txn.txn_id in used_txns:
                continue

            score, expl = compute_match_score(
                receipt_amt=r_amt,
                receipt_date_str=r_date,
                receipt_store=r_store,
                txn=txn,
                days_window=days_window,
                amt_tolerance=amt_tolerance
            )

            if score > best_score:
                best_score = score
                best_txn = txn
                best_expl = expl

        if best_txn and best_score >= 0.70:
            used_txns.add(best_txn.txn_id)
            status = "Verified Match" if best_score >= 0.85 else "Probable Match"
            matches.append(ReconciliationMatch(
                receipt_id=r_id,
                receipt_store=r_store,
                receipt_date=r_date,
                receipt_amount=r_amt,
                txn_id=best_txn.txn_id,
                txn_description=best_txn.description,
                txn_date=best_txn.date,
                txn_amount=best_txn.amount,
                confidence_score=best_score,
                match_status=status,
                explanation=best_expl
            ))
        else:
            matches.append(ReconciliationMatch(
                receipt_id=r_id,
                receipt_store=r_store,
                receipt_date=r_date,
                receipt_amount=r_amt,
                txn_id="—",
                txn_description="No matching bank transaction found",
                txn_date="—",
                txn_amount=0.0,
                confidence_score=0.0,
                match_status="Unmatched",
                explanation="No transaction satisfied amount, date, and merchant tolerance thresholds"
            ))

    return matches


def evaluate_statement_matcher_benchmark() -> Dict[str, float]:
    """
    Evaluate precision and recall of the matching engine on synthetic bank statement test pairs.
    """
    ground_truth_pairs = [
        # (Receipt store, date, amount, Txn desc, txn date, txn amount, is_true_match)
        ("SUPERMART RETAIL", "2026-10-18", 1349.25, "POS/SUPERMART RETAIL/BLR/9402", "2026-10-19", 1349.25, True),
        ("APOLLO MEDPLUS PHARMACY", "2026-10-17", 465.36, "UPI/APOLLO PHARMACY/KORAMANGALA", "2026-10-17", 465.36, True),
        ("THE ARTISAN CAFE & BISTRO", "2026-10-16", 1239.00, "DEBIT CARD/ARTISAN BISTRO", "2026-10-18", 1239.00, True),
        ("DIGITAL TECH HUB", "2026-10-15", 2591.28, "IMPS/DIGITAL TECH HUB/58291", "2026-10-15", 2591.28, True),
        ("LOCAL GROCER", "2026-10-10", 350.00, "UPI/SWIGGY/ORDER3940", "2026-10-10", 350.00, False),  # Amount matches unrelated vendor
        ("SHELL PETROL PUMP", "2026-10-05", 2000.00, "POS/SHELL FUEL/1029", "2026-10-12", 2000.00, False), # Outside 3-day window
    ]

    receipts = []
    txns = []
    true_matches_map = {}

    for idx, (r_store, r_date, r_amt, t_desc, t_date, t_amt, is_match) in enumerate(ground_truth_pairs):
        r_id = f"REC-BENCH-{idx+1:03d}"
        t_id = f"TXN-BENCH-{idx+1:03d}"
        receipts.append({"id": r_id, "store": r_store, "date": r_date, "total": r_amt})
        txns.append(BankTransaction(txn_id=t_id, date=t_date, description=t_desc, amount=t_amt))
        true_matches_map[r_id] = t_id if is_match else None

    matches = match_receipts_to_statement(receipts, txns, days_window=3, amt_tolerance=1.0)

    tp = 0
    fp = 0
    fn = 0

    for m in matches:
        expected_txn = true_matches_map[m.receipt_id]
        if m.match_status in ("Verified Match", "Probable Match"):
            if m.txn_id == expected_txn:
                tp += 1
            else:
                fp += 1
        else:
            if expected_txn is not None:
                fn += 1

    precision = tp / max(tp + fp, 1e-6)
    recall = tp / max(tp + fn, 1e-6)
    f1 = 2 * precision * recall / max(precision + recall, 1e-6)

    return {
        "precision": round(precision * 100.0, 1),
        "recall": round(recall * 100.0, 1),
        "f1": round(f1 * 100.0, 1),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


if __name__ == "__main__":
    res = evaluate_statement_matcher_benchmark()
    print("=" * 60)
    print("Bank Statement Reconciliation Performance Benchmark:")
    print(f"  Precision: {res['precision']}%")
    print(f"  Recall:    {res['recall']}%")
    print(f"  F1 Score:  {res['f1']}%")
    print("=" * 60)
