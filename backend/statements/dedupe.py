"""Stable de-duplication keys so re-imports, overlapping statements and bank+UPI-app copies collapse."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import List, Optional

from backend.statements.narration import normalize


def account_key(source: str, hint: Optional[str], label: str = "") -> str:
    return f"{source or 'generic'}:{hint or normalize(label) or 'default'}"


def _clean_ref(ref: Optional[str]) -> str:
    r = re.sub(r"[^A-Za-z0-9]", "", ref or "").upper().lstrip("0")
    return r if len(r) >= 6 else ""


def assign_keys(txns: List[dict], acct: str) -> None:
    """Set ``dedupe_key`` on each transaction (UTR > bank reference > content hash with occurrence index)."""
    seen: Counter = Counter()
    for t in txns:
        d = t["direction"]
        ref = _clean_ref(t.get("reference"))
        utr = ref if re.fullmatch(r"\d{12}", ref) else (t.get("utr") if t.get("channel") in ("UPI", "IMPS") else None)
        if utr:
            t["dedupe_key"] = f"utr:{d}:{utr}"
        elif ref:
            t["dedupe_key"] = f"ref:{acct}:{d}:{ref}"
        else:
            bal = "" if t.get("balance") is None else f"{t['balance']:.2f}"
            raw = f"{t['date']}|{t['amount']:.2f}|{d}|{normalize(t['description'])}|{bal}"
            h = hashlib.sha1(raw.encode()).hexdigest()[:20]
            seen[h] += 1
            t["dedupe_key"] = f"h:{acct}:{h}#{seen[h]}"
