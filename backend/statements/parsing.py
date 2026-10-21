"""Read CSV / XLSX / XLS / PDF statements into a raw table, detect columns and turn rows into transactions."""

from __future__ import annotations

import csv
import io
import re
from datetime import date as date_cls
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backend import config
from backend.files import UploadError

Rows = List[List[str]]
ROLES = ("date", "description", "debit", "credit", "amount", "drcr", "balance", "reference")

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}
_MAX_COLS = 40
_SKIP_DESC = re.compile(r"^\s*(opening balance|closing balance|total|grand total|balance b/?f|balance c/?f|"
                        r"brought forward|carried forward|statement summary)\b", re.I)


class StatementError(UploadError):
    """Statement could not be read; message is safe to show."""

    def __init__(self, message: str, code: Optional[str] = None, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# ─── Sniffing / reading ─────────────────────────────────────────────────────
def sniff(data: bytes) -> str:
    if not data:
        raise StatementError("The uploaded file is empty.")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        if b"xl/workbook" in data[:200000] or b"xl/" in data[:200000]:
            return "xlsx"
        raise StatementError("Unsupported file. Upload a CSV, Excel (XLSX/XLS) or PDF statement.")
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "xls"
    if b"\x00" in data[:4096]:
        raise StatementError("Unsupported file. Upload a CSV, Excel (XLSX/XLS) or PDF statement.")
    return "csv"


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date_cls):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else f"{v:.2f}"
    return str(v).strip()


def _cap(rows: Rows) -> Rows:
    out = []
    for r in rows:
        r = [_cell(c) for c in r[:_MAX_COLS]]
        if any(r):
            out.append(r)
        if len(out) > config.MAX_STATEMENT_ROWS + 200:
            raise StatementError(f"Statement has too many rows (max {config.MAX_STATEMENT_ROWS}).")
    return out


def read_csv(data: bytes) -> Rows:
    text = _decode(data)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return _cap(csv.reader(io.StringIO(text), dialect))


def read_xlsx(data: bytes) -> Rows:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise StatementError("Could not read the Excel file.") from exc
    try:
        ws = wb.worksheets[0]
        return _cap(ws.iter_rows(values_only=True))
    finally:
        wb.close()


def read_xls(data: bytes) -> Rows:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise StatementError("Could not read the Excel file.") from exc
    sh = book.sheet_by_index(0)
    rows = []
    for i in range(min(sh.nrows, config.MAX_STATEMENT_ROWS + 200)):
        row = []
        for c in sh.row(i):
            if c.ctype == xlrd.XL_CELL_DATE:
                row.append(xlrd.xldate.xldate_as_datetime(c.value, book.datemode))
            else:
                row.append(c.value)
        rows.append(row)
    return _cap(rows)


# ─── Values ─────────────────────────────────────────────────────────────────
def parse_date(s: str, order: str = "dmy") -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    s = re.sub(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\s*[ap]\.?m\.?)?$", "", s, flags=re.I).strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[-/. ]([A-Za-z]{3,9})[-/., ]+(\d{2,4})", s)
    if m:
        mon = _MONTHS.get(m[2][:3].lower())
        return _mk(_year(m[3]), mon, int(m[1])) if mon else None
    m = re.fullmatch(r"([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})", s)
    if m:
        mon = _MONTHS.get(m[1][:3].lower())
        return _mk(int(m[3]), mon, int(m[2])) if mon else None
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        return _mk(y, b, a) if order != "mdy" else _mk(y, a, b)
    return None


def _year(y: str) -> int:
    n = int(y)
    return 2000 + n if n < 100 else n


def _mk(y: int, m: int, d: int) -> Optional[str]:
    try:
        return date_cls(y, m, d).isoformat()
    except ValueError:
        return None


def parse_amount(s: str) -> Optional[Tuple[float, Optional[str]]]:
    """Return (signed value, 'debit'|'credit'|None hint) or None when the cell is not an amount."""
    t = (s or "").strip()
    if not t or t in ("-", "--"):
        return None
    hint = None
    m = re.search(r"\b(dr|cr)\.?$", t, re.I)
    if m:
        hint = "debit" if m[1].lower() == "dr" else "credit"
        t = t[: m.start()].strip()
    t = re.sub(r"^(₹|rs\.?|inr)\s*", "", t, flags=re.I).replace("₹", "").strip()
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.startswith("-"):
        neg, t = True, t[1:]
    elif t.startswith("+"):
        t = t[1:]
    t = re.sub(r"^(₹|rs\.?|inr)\s*", "", t.strip(), flags=re.I).replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return (-v if neg else v), hint


# ─── Header detection ───────────────────────────────────────────────────────
def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (h or "").lower())).strip()


def _role(h: str) -> Optional[str]:
    n = _norm(h)
    if not n:
        return None
    w = set(n.split())
    if n in ("dr cr", "cr dr", "debit credit", "type", "transaction type", "txn type", "d c"):
        return "drcr"
    if "balance" in w:
        return "balance"
    if "date" in w or n in ("txn dt", "tran dt", "value dt"):
        return "value_date" if ("value" in w) else "date"
    if w & {"narration", "description", "particulars", "remarks", "details", "transaction details"} or \
            n in ("transaction remarks", "transaction details", "details"):
        return "description"
    if w & {"withdrawal", "withdrawals", "debit", "debits", "dr"} or n in ("paid out", "amount debited"):
        return "debit"
    if w & {"deposit", "deposits", "credit", "credits", "cr"} or n in ("paid in", "amount credited"):
        return "credit"
    if w & {"ref", "reference", "chq", "cheque", "utr"}:
        return "reference"
    if w & {"amount", "amt"}:
        return "amount"
    return None


def detect_mapping(rows: Rows) -> Tuple[Optional[dict], float]:
    best, best_conf = None, 0.0
    for i, row in enumerate(rows[:40]):
        roles: Dict[str, int] = {}
        value_date = None
        for j, cell in enumerate(row):
            r = _role(cell)
            if r == "value_date":
                value_date = j if value_date is None else value_date
            elif r and r not in roles:
                roles[r] = j
        if "date" not in roles and value_date is not None:
            roles["date"] = value_date
        if not ({"date", "description"} <= roles.keys() and roles.keys() & {"debit", "credit", "amount"}):
            continue
        mapping = {"header_row": i, "date_order": "dmy", **{k: roles.get(k) for k in ROLES}}
        mapping["date_order"] = _guess_order(rows[i + 1:], roles["date"])
        conf = _confidence(rows[i + 1:], mapping)
        if conf > best_conf:
            best, best_conf = mapping, conf
    return best, best_conf


def _guess_order(rows: Rows, col: int) -> str:
    for r in rows[:200]:
        m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.]\d{2,4}", (r[col] if col < len(r) else "").strip())
        if m and int(m[2]) > 12:
            return "mdy"
    return "dmy"


def _confidence(rows: Rows, mapping: dict) -> float:
    dated = ok = 0
    signed = _is_signed(rows, mapping)
    for r in rows:
        d = parse_date(_get(r, mapping["date"]), mapping["date_order"])
        if not d:
            continue
        dated += 1
        if _row_amount(r, mapping, signed) is not None:
            ok += 1
    return ok / dated if dated else 0.0


def _get(row: List[str], idx: Optional[int]) -> str:
    return row[idx].strip() if idx is not None and 0 <= idx < len(row) else ""


def _row_amount(r: List[str], m: dict, signed: bool = False) -> Optional[Tuple[float, str]]:
    deb = parse_amount(_get(r, m.get("debit"))) if m.get("debit") is not None else None
    cre = parse_amount(_get(r, m.get("credit"))) if m.get("credit") is not None else None
    if deb and abs(deb[0]) > 0:
        return abs(deb[0]), "debit"
    if cre and abs(cre[0]) > 0:
        return abs(cre[0]), "credit"
    if m.get("amount") is not None:
        a = parse_amount(_get(r, m["amount"]))
        if not a or a[0] == 0:
            return None
        direction = a[1]
        if m.get("drcr") is not None:
            t = _get(r, m["drcr"]).lower()
            if t.startswith(("dr", "debit", "d", "withdraw")):
                direction = "debit"
            elif t.startswith(("cr", "credit", "c", "deposit")):
                direction = "credit"
        if direction is None:
            # Signed column (has negatives): negative = debit, positive = credit; otherwise all debits
            direction = "debit" if a[0] < 0 or not signed else "credit"
        return abs(a[0]), direction
    return None


def _is_signed(rows: Rows, m: dict) -> bool:
    if m.get("amount") is None:
        return False
    for r in rows:
        a = parse_amount(_get(r, m["amount"]))
        if a and a[0] < 0:
            return True
    return False


def validate_mapping(mapping: dict, ncols: int, nrows: int) -> None:
    if mapping.get("date") is None or mapping.get("description") is None:
        raise StatementError("Choose the date and description columns.", status=422)
    if all(mapping.get(k) is None for k in ("debit", "credit", "amount")):
        raise StatementError("Choose a debit/credit or amount column.", status=422)
    used = [mapping[k] for k in ROLES if mapping.get(k) is not None]
    if len(used) != len(set(used)):
        raise StatementError("Each column can be used only once.", status=422)
    if any(not 0 <= i < ncols for i in used):
        raise StatementError("Column index out of range.", status=422)
    if not -1 <= mapping.get("header_row", -1) < nrows:
        raise StatementError("Header row out of range.", status=422)


def apply_mapping(rows: Rows, mapping: dict) -> Tuple[List[dict], int]:
    """Rows after the header -> transactions. Returns (transactions, skipped_rows)."""
    out: List[dict] = []
    skipped = 0
    order = mapping.get("date_order") or "dmy"
    body = rows[mapping.get("header_row", -1) + 1:]
    signed = _is_signed(body, mapping)
    for r in body:
        desc = re.sub(r"\s+", " ", _get(r, mapping["description"]))
        d = parse_date(_get(r, mapping["date"]), order)
        amt = _row_amount(r, mapping, signed)
        if not d:
            if out and desc and amt is None and not _SKIP_DESC.match(desc):
                out[-1]["description"] = (out[-1]["description"] + " " + desc).strip()[:500]
            elif any(c.strip() for c in r):
                skipped += 1
            continue
        if _SKIP_DESC.match(desc) or amt is None:
            skipped += 1
            continue
        bal = parse_amount(_get(r, mapping.get("balance")))
        out.append({"date": d, "description": desc[:500] or "(no description)", "amount": round(amt[0], 2),
                    "direction": amt[1], "balance": round(bal[0], 2) if bal else None,
                    "reference": _get(r, mapping.get("reference"))[:60] or None})
    if len(out) > config.MAX_STATEMENT_ROWS:
        raise StatementError(f"Statement has too many rows (max {config.MAX_STATEMENT_ROWS}).")
    return out, skipped


# ─── Source / account hints ─────────────────────────────────────────────────
_SOURCES = [("phonepe", r"phonepe"), ("gpay", r"google pay|\bgpay\b"), ("paytm", r"paytm"),
            ("hdfc", r"hdfc"), ("icici", r"icici"), ("sbi", r"state bank|\bsbi\b"), ("axis", r"axis bank"),
            ("kotak", r"kotak")]


def detect_source(text: str) -> Tuple[str, Optional[str]]:
    low = text[:4000].lower()
    head = low[:600]  # narrations mention other banks' IFSC codes; the statement header names the issuer
    hits = [(m.start(), name) for name, pat in _SOURCES for m in [re.search(pat, head)] if m]
    source = min(hits)[1] if hits else "generic"
    m = re.search(r"(?:a/c|acct|account)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([x*\d][x*\d -]{3,24}\d)", low)
    hint = None
    if m:
        digits = re.sub(r"\D", "", m[1])
        if len(digits) >= 4:
            hint = "XX" + digits[-4:]
    return source, hint
