"""Bill splitting (Splitwise-style): people, per-receipt splits, balances and settlements.

Money is handled in integer minor units (paise/cents). ``person_id = None`` always means "You".
Balance sign convention: per person and currency, ``net > 0`` means they owe you.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import storage
from backend import db

METHODS = ("equal", "exact", "percent", "shares", "items")
RESERVED_NAMES = {"you", "me"}
_EXP0 = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "PYG"}
_EXP3 = {"BHD", "KWD", "OMR", "JOD", "TND"}


class SplitError(ValueError):
    """Invalid split input (mapped to HTTP 422)."""


class NotFound(LookupError):
    """Missing receipt, split or person (mapped to HTTP 404)."""


# ─── Money helpers ──────────────────────────────────────────────────────────
def minor_exp(currency: str) -> int:
    cur = (currency or "").upper()
    return 0 if cur in _EXP0 else 3 if cur in _EXP3 else 2


def to_minor(amount: float, currency: str) -> int:
    q = Decimal(str(amount)).scaleb(minor_exp(currency))
    return int(q.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_minor(n: int, currency: str) -> float:
    return round(n / (10 ** minor_exp(currency)), minor_exp(currency))


def allocate(total_minor: int, weights: List[float]) -> List[int]:
    """Largest-remainder split of ``total_minor`` by ``weights``; the result always sums to the total."""
    if total_minor < 0:
        return [-x for x in allocate(-total_minor, weights)]
    ws = [Fraction(repr(float(w))) for w in weights]
    s = sum(ws)
    if not ws or s <= 0:
        raise SplitError("Split weights must add up to more than zero.")
    raw = [Fraction(total_minor) * w / s for w in ws]
    out = [int(r // 1) for r in raw]
    left = total_minor - sum(out)
    order = sorted(range(len(ws)), key=lambda i: (-(raw[i] - out[i]), i))
    for i in order[:left]:
        out[i] += 1
    return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─── People ─────────────────────────────────────────────────────────────────
def list_people() -> List[dict]:
    db.init()
    with storage.get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT id, name FROM people ORDER BY name COLLATE NOCASE")]


def add_person(name: str) -> Tuple[dict, bool]:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 60:
        raise SplitError("Name must be 1-60 characters.")
    if name.lower() in RESERVED_NAMES:
        raise SplitError(f"'{name}' is reserved for you. Pick another name.")
    db.init()
    with storage.get_connection() as conn:
        row = conn.execute("SELECT id, name FROM people WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return dict(row), False
        cur = conn.execute("INSERT INTO people (name, created_at) VALUES (?, ?)", (name, _now()))
        return {"id": cur.lastrowid, "name": name}, True


def delete_person(person_id: int) -> None:
    """Delete a person who has no splits or settlements. Raises NotFound / SplitError."""
    db.init()
    with storage.get_connection() as conn:
        if not conn.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
            raise NotFound("Person not found")
        used = conn.execute(
            "SELECT 1 FROM splits WHERE payer_id = ? UNION SELECT 1 FROM split_shares WHERE person_id = ? "
            "UNION SELECT 1 FROM settlements WHERE person_id = ? LIMIT 1", (person_id,) * 3).fetchone()
        if used:
            raise SplitError("This person is part of split bills or settlements and can't be deleted.")
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))


def _names(conn) -> Dict[Optional[int], str]:
    names: Dict[Optional[int], str] = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM people")}
    names[None] = "You"
    return names


# ─── Split computation ──────────────────────────────────────────────────────
def compute(receipt: dict, payer_id: Optional[int], method: str, participants: List[dict],
            items: Optional[List[dict]] = None, known_ids: Optional[set] = None) -> dict:
    """Validate inputs and compute each party's share in minor units."""
    cur = receipt["currency"]
    total = to_minor(receipt["total"] or 0, cur)
    if total <= 0:
        raise SplitError("The receipt total must be greater than zero to split it.")
    if method not in METHODS:
        raise SplitError(f"method must be one of {METHODS}")
    pids = [p.get("person_id") for p in participants]
    if not pids:
        raise SplitError("Add at least one participant.")
    if len(set(pids)) != len(pids):
        raise SplitError("Each person can appear only once.")
    if known_ids is not None:
        unknown = {p for p in pids + [payer_id] if p is not None and p not in known_ids}
        if unknown:
            raise SplitError(f"Unknown person id(s): {sorted(unknown)}")
    if payer_id is not None and payer_id not in pids:
        raise SplitError("The payer must be one of the people in the split.")
    if payer_id is not None and None not in pids:
        raise SplitError("You must be the payer or one of the people in the split.")
    if len(set(pids) | {payer_id}) < 2:
        raise SplitError("A split needs at least two people.")

    values = [p.get("value") for p in participants]
    inputs: Dict[Optional[int], Optional[float]] = {pid: None for pid in pids}

    if method == "equal":
        amounts = allocate(total, [1] * len(pids))
    elif method == "exact":
        amounts = []
        for v in values:
            if v is None or v < 0:
                raise SplitError("Enter an amount of zero or more for everyone.")
            amounts.append(to_minor(v, cur))
        diff = total - sum(amounts)
        if diff:
            word = "left to assign" if diff > 0 else "over the total"
            raise SplitError(f"Amounts must add up to the total: {from_minor(abs(diff), cur):.{minor_exp(cur)}f} {cur} {word}.")
        inputs = dict(zip(pids, [float(v) for v in values]))
    elif method in ("percent", "shares"):
        for v in values:
            if v is None or v < 0 or (method == "shares" and v <= 0):
                raise SplitError("Shares must be greater than zero." if method == "shares"
                                 else "Enter a percentage of zero or more for everyone.")
        if method == "percent" and round(sum(values), 2) != 100:
            raise SplitError(f"Percentages must add up to 100 (currently {round(sum(values), 2):g}).")
        amounts = allocate(total, values)
        inputs = dict(zip(pids, [float(v) for v in values]))
    else:  # items
        line_items = receipt.get("items") or []
        if not line_items:
            raise SplitError("This receipt has no line items to split by item.")
        assign = {}
        for a in items or []:
            idx = a.get("item_index")
            if idx in assign:
                raise SplitError(f"Item {idx + 1} is assigned more than once.")
            if not isinstance(idx, int) or not 0 <= idx < len(line_items):
                raise SplitError("Item assignments don't match this receipt's items.")
            who = list(dict.fromkeys(a.get("person_ids") or []))
            if not who:
                raise SplitError(f"Pick at least one person for '{line_items[idx]['item_name']}'.")
            if any(w not in pids for w in who):
                raise SplitError("Items can only be assigned to people in the split.")
            assign[idx] = who
        if len(assign) != len(line_items):
            raise SplitError("Assign every item to at least one person.")
        sub: Dict[Optional[int], int] = {pid: 0 for pid in pids}
        for idx, who in assign.items():
            for w, part in zip(who, allocate(to_minor(line_items[idx]["amount"], cur), [1] * len(who))):
                sub[w] += part
        if any(v < 0 for v in sub.values()) or sum(sub.values()) <= 0:
            raise SplitError("Each person's items must add up to zero or more, and the items to more than zero.")
        amounts = allocate(total, [sub[p] for p in pids])

    shares = dict(zip(pids, amounts))
    your_share = shares.get(None, 0)
    you_lent, you_owe = (total - your_share, 0) if payer_id is None else (0, your_share)
    return {"total": total, "shares": shares, "inputs": inputs, "your_share": your_share,
            "you_lent": you_lent, "you_owe": you_owe}


def _item_weights(receipt: dict, method: str, comp: dict, assign: Dict[int, list]) -> List[float]:
    """Per line item weight so that sum(amount * weight) == your share."""
    cur, total, mine = receipt["currency"], comp["total"], comp["your_share"]
    items = receipt.get("items") or []
    if method != "items":
        return [mine / total] * len(items)
    yours = []
    for idx, it in enumerate(items):
        who = assign.get(idx, [])
        parts = allocate(to_minor(it["amount"], cur), [1] * len(who)) if who else []
        yours.append(dict(zip(who, parts)).get(None, 0))
    my_sub = sum(yours)
    out = []
    for idx, it in enumerate(items):
        item_minor = to_minor(it["amount"], cur)
        out.append(0.0 if not item_minor or not my_sub else (yours[idx] / item_minor) * (mine / my_sub))
    return out


# ─── Persistence ────────────────────────────────────────────────────────────
def save_split(receipt_id: str, payload: dict) -> dict:
    """Create or replace the split for a receipt. ``payload``: payer_id, method, participants, items."""
    receipt = storage.get_receipt(receipt_id)
    if not receipt:
        raise NotFound("Receipt not found")
    method = payload.get("method", "equal")
    participants = payload.get("participants") or []
    items = payload.get("items") or [] if method == "items" else []
    payer_id = payload.get("payer_id")
    with storage.get_connection() as conn:
        known = {r[0] for r in conn.execute("SELECT id FROM people")}
        comp = compute(receipt, payer_id, method, participants, items, known)
        assign = {a["item_index"]: list(dict.fromkeys(a["person_ids"])) for a in items}
        old = conn.execute("SELECT created_at FROM splits WHERE receipt_id = ?", (receipt_id,)).fetchone()
        conn.execute("DELETE FROM splits WHERE receipt_id = ?", (receipt_id,))
        now = _now()
        sid = conn.execute(
            "INSERT INTO splits (receipt_id, payer_id, method, total_minor, currency, your_share_minor, "
            "you_lent_minor, you_owe_minor, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (receipt_id, payer_id, method, comp["total"], receipt["currency"], comp["your_share"],
             comp["you_lent"], comp["you_owe"], old["created_at"] if old else now, now)).lastrowid
        for pid, amt in comp["shares"].items():
            conn.execute("INSERT INTO split_shares (split_id, person_id, share_minor, input_value) VALUES (?,?,?,?)",
                         (sid, pid, amt, comp["inputs"].get(pid)))
        for idx, who in assign.items():
            for pid in who:
                conn.execute("INSERT INTO split_items (split_id, item_index, person_id) VALUES (?,?,?)",
                             (sid, idx, pid))
        conn.execute("UPDATE receipts SET my_share = ? WHERE id = ?",
                     (from_minor(comp["your_share"], receipt["currency"]), receipt_id))
        for it, w in zip(receipt["items"], _item_weights(receipt, method, comp, assign)):
            conn.execute("UPDATE line_items SET share_weight = ? WHERE id = ?", (w, it["id"]))
    return get_split(receipt_id)


def delete_split(receipt_id: str) -> bool:
    db.init()
    with storage.get_connection() as conn:
        n = conn.execute("DELETE FROM splits WHERE receipt_id = ?", (receipt_id,)).rowcount
        conn.execute("UPDATE receipts SET my_share = NULL WHERE id = ?", (receipt_id,))
        conn.execute("UPDATE line_items SET share_weight = 1.0 WHERE receipt_id = ?", (receipt_id,))
    return n > 0


def _stored_inputs(conn, split_id: int) -> Tuple[List[dict], List[dict]]:
    parts = [{"person_id": r["person_id"], "value": r["input_value"]} for r in conn.execute(
        "SELECT person_id, input_value FROM split_shares WHERE split_id = ? ORDER BY id", (split_id,))]
    grouped: Dict[int, list] = defaultdict(list)
    for r in conn.execute("SELECT item_index, person_id FROM split_items WHERE split_id = ? ORDER BY rowid",
                          (split_id,)):
        grouped[r["item_index"]].append(r["person_id"])
    return parts, [{"item_index": k, "person_ids": v} for k, v in sorted(grouped.items())]


def get_split(receipt_id: str) -> Optional[dict]:
    db.init()
    with storage.get_connection() as conn:
        s = conn.execute("SELECT * FROM splits WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if not s:
            return None
        names = _names(conn)
        cur = s["currency"]
        shares = [{"person_id": r["person_id"], "name": names.get(r["person_id"], "?"),
                   "amount": from_minor(r["share_minor"], cur), "value": r["input_value"]}
                  for r in conn.execute("SELECT * FROM split_shares WHERE split_id = ? ORDER BY id", (s["id"],))]
        _, items = _stored_inputs(conn, s["id"])
    return {"receipt_id": receipt_id, "method": s["method"], "currency": cur,
            "total": from_minor(s["total_minor"], cur),
            "payer": {"id": s["payer_id"], "name": names.get(s["payer_id"], "?")},
            "your_share": from_minor(s["your_share_minor"], cur), "you_lent": from_minor(s["you_lent_minor"], cur),
            "you_owe": from_minor(s["you_owe_minor"], cur), "shares": shares, "items": items,
            "created_at": s["created_at"], "updated_at": s["updated_at"]}


def recompute(receipt_id: str) -> bool:
    """Re-apply a stored split after a receipt edit. Returns True if it no longer fits and was removed."""
    db.init()
    with storage.get_connection() as conn:
        s = conn.execute("SELECT id, payer_id, method FROM splits WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if not s:
            return False
        parts, items = _stored_inputs(conn, s["id"])
    try:
        save_split(receipt_id, {"payer_id": s["payer_id"], "method": s["method"], "participants": parts,
                                "items": items})
        return False
    except SplitError:
        delete_split(receipt_id)
        return True


# ─── Listing, balances, settlements ─────────────────────────────────────────
def list_splits() -> List[dict]:
    db.init()
    with storage.get_connection() as conn:
        rows = conn.execute(
            "SELECT s.*, r.store, r.date, p.name payer_name FROM splits s JOIN receipts r ON r.id = s.receipt_id "
            "LEFT JOIN people p ON p.id = s.payer_id ORDER BY r.date DESC, s.created_at DESC").fetchall()
    out = []
    for s in rows:
        cur = s["currency"]
        out.append({"receipt_id": s["receipt_id"], "store": s["store"], "date": s["date"], "method": s["method"],
                    "currency": cur, "total": from_minor(s["total_minor"], cur),
                    "payer": s["payer_name"] or "You", "your_share": from_minor(s["your_share_minor"], cur),
                    "you_lent": from_minor(s["you_lent_minor"], cur), "you_owe": from_minor(s["you_owe_minor"], cur)})
    return out


def balances() -> dict:
    db.init()
    net: Dict[Tuple[int, str], int] = defaultdict(int)
    with storage.get_connection() as conn:
        names = _names(conn)
        # Splits you paid: everyone else owes you their share
        for r in conn.execute("SELECT sh.person_id, s.currency, SUM(sh.share_minor) a FROM split_shares sh "
                              "JOIN splits s ON s.id = sh.split_id WHERE s.payer_id IS NULL AND sh.person_id "
                              "IS NOT NULL GROUP BY sh.person_id, s.currency"):
            net[(r["person_id"], r["currency"])] += r["a"]
        # Splits someone else paid: you owe them your share
        for r in conn.execute("SELECT s.payer_id, s.currency, SUM(s.your_share_minor) a FROM splits s "
                              "WHERE s.payer_id IS NOT NULL GROUP BY s.payer_id, s.currency"):
            net[(r["payer_id"], r["currency"])] -= r["a"]
        for r in conn.execute("SELECT person_id, currency, direction, SUM(amount_minor) a FROM settlements "
                              "GROUP BY person_id, currency, direction"):
            net[(r["person_id"], r["currency"])] += r["a"] if r["direction"] == "you_paid" else -r["a"]
    people, totals = [], defaultdict(lambda: [0, 0])
    for (pid, cur), n in sorted(net.items(), key=lambda kv: (names.get(kv[0][0], "").lower(), kv[0][1])):
        if n == 0:
            continue
        people.append({"person_id": pid, "name": names.get(pid, "?"), "currency": cur, "net": from_minor(n, cur)})
        totals[cur][0 if n > 0 else 1] += abs(n)
    return {"totals": [{"currency": c, "owed_to_you": from_minor(t[0], c), "you_owe": from_minor(t[1], c)}
                       for c, t in sorted(totals.items())],
            "people": people}


def add_settlement(person_id: int, direction: str, amount: float, currency: str,
                   date: Optional[str] = None, note: str = "") -> dict:
    if direction not in ("you_paid", "they_paid"):
        raise SplitError("direction must be you_paid or they_paid")
    currency = storage.to_iso_currency(currency)
    amount_minor = to_minor(amount, currency)
    if amount_minor <= 0:
        raise SplitError("Amount must be greater than zero.")
    date = date or date_cls.today().isoformat()
    db.init()
    with storage.get_connection() as conn:
        p = conn.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not p:
            raise NotFound("Person not found")
        sid = conn.execute(
            "INSERT INTO settlements (person_id, direction, amount_minor, currency, date, note, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (person_id, direction, amount_minor, currency, date, note or "", _now())).lastrowid
    return {"id": sid, "person_id": person_id, "name": p["name"], "direction": direction,
            "amount": from_minor(amount_minor, currency), "currency": currency, "date": date, "note": note or ""}
