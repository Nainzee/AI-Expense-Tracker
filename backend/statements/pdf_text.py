"""Text PDFs (bank statements and UPI-app statements) -> raw table rows. Scanned PDFs are rejected."""

from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

from backend import config
from backend.statements.parsing import Rows, StatementError, detect_mapping, parse_amount, parse_date

CANONICAL_HEADER = ["Date", "Description", "Debit", "Credit", "Balance", "Reference"]

_DATE = r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[- ][A-Za-z]{3}[- ,]+\d{2,4}|[A-Za-z]{3} \d{1,2},? \d{4})"
_AMT = r"(?:(?:Rs\.?|₹|INR)\s*)?\(?-?\d[\d,]*\.\d{2}\)?(?:\s?(?:Dr|Cr|DR|CR)\.?)?"
_DATE_LINE = re.compile(rf"^{_DATE}\s+(.*)$")
_TRAILING_AMT = re.compile(rf"\s+({_AMT})$")
_OPENING = re.compile(rf"opening balance\s*:?\s*({_AMT})", re.I)
_FOOTER = re.compile(r"(page \d+ (of|/) \d+|statement of account|computer generated|closing balance|"
                     r"^date\b.*\bbalance\b|^total\b|generated on|this is a system)", re.I)
_UPI_TXN = re.compile(rf"^{_DATE}\s+((?:paid to|received from|transfer to|money sent to|payment to|refund from)"
                      r"\s.+?)\s+(debit|credit)\s+(?:(?:rs\.?|₹|inr)\s*)?([\d,]+(?:\.\d{1,2})?)$", re.I)
_UTR = re.compile(r"utr(?: no\.?| number)?\s*[:.]?\s*(\d{12})", re.I)
_CREDIT_WORDS = re.compile(r"\b(salary|received|refund|reversal|cashback|interest|deposit|credit|neft cr|by transfer)\b",
                           re.I)


def _is_password_error(exc: BaseException) -> bool:
    seen = 0
    while exc is not None and seen < 6:
        if type(exc).__name__ in ("PDFPasswordIncorrect", "PDFEncryptionError"):
            return True
        if any(isinstance(a, BaseException) and _is_password_error(a) for a in getattr(exc, "args", ())):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def read_pdf(data: bytes, password: Optional[str] = None) -> Tuple[Rows, str]:
    """Return (rows, full_text). Rows are either the PDF's own table or CANONICAL_HEADER + parsed lines."""
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password or "")
        n_pages = len(pdf.pages)
    except Exception as exc:
        if _is_password_error(exc):
            if password:
                raise StatementError("That password is incorrect.", code="password_incorrect", status=422)
            raise StatementError("This PDF is password protected. Enter its password.", code="password_required",
                                 status=422)
        raise StatementError("Could not read the PDF file.") from exc
    try:
        if n_pages > config.MAX_STATEMENT_PAGES:
            raise StatementError(f"PDF has too many pages (max {config.MAX_STATEMENT_PAGES}).")
        texts: List[str] = []
        table_rows: Rows = []
        positional: Rows = []
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
            for table in page.extract_tables() or []:
                table_rows.extend([[(c or "").replace("\n", " ").strip() for c in row] for row in table])
            positional.extend(_positional_rows(page))
    except StatementError:
        raise
    except Exception as exc:
        raise StatementError("Could not read the PDF file.") from exc
    finally:
        pdf.close()
    text = "\n".join(texts)
    if not text.strip():
        raise StatementError("This PDF looks scanned (no text layer). Download a text PDF, CSV or Excel "
                             "statement from your bank instead.")
    if table_rows:
        mapping, conf = detect_mapping(table_rows)
        if mapping and conf >= 0.8:
            return table_rows, text
    # Column-position parsing is the most reliable for bank PDFs whose narrations wrap over several
    # lines (e.g. ICICI puts the payee name above the date line and the reference below it).
    if positional:
        return [CANONICAL_HEADER] + positional, text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows = _upi_rows(lines) or _bank_rows(lines)
    if not rows:
        raise StatementError("No transactions found in this PDF.")
    return [CANONICAL_HEADER] + rows, text


def _upi_rows(lines: List[str]) -> Rows:
    rows: Rows = []
    for ln in lines:
        m = _UPI_TXN.match(ln)
        if m:
            d = parse_date(m[1])
            if not d:
                continue
            debit = m[3].lower() == "debit"
            amt = m[4].replace(",", "")
            rows.append([d, m[2].strip(), amt if debit else "", "" if debit else amt, "", ""])
            continue
        u = _UTR.search(ln)
        if u and rows and not rows[-1][5]:
            rows[-1][5] = u[1]
    return rows


def _bank_rows(lines: List[str]) -> Rows:
    rows: Rows = []
    prev_bal: Optional[float] = None
    last_is_txn = False
    for ln in lines:
        o = _OPENING.search(ln)
        if o:
            a = parse_amount(o[1])
            prev_bal = abs(a[0]) if a else prev_bal
            last_is_txn = False
            continue
        m = _DATE_LINE.match(ln)
        d = parse_date(m[1]) if m else None
        if not d:
            if last_is_txn and not _FOOTER.search(ln) and not _TRAILING_AMT.search(" " + ln):
                rows[-1][1] = (rows[-1][1] + " " + ln).strip()
            else:
                last_is_txn = False
            continue
        rest = m[2]
        vd = re.match(rf"^{_DATE}\s+", rest)  # value date column
        if vd and parse_date(vd[1]):
            rest = rest[vd.end():]
        amts: List[str] = []
        while len(amts) < 3:
            t = _TRAILING_AMT.search(rest)
            if not t:
                break
            amts.insert(0, t[1])
            rest = rest[: t.start()]
        parsed = [parse_amount(a) for a in amts]
        parsed = [p for p in parsed if p]
        if not parsed:
            last_is_txn = False
            continue
        bal = abs(parsed[-1][0]) if len(parsed) >= 2 else None
        cand = [p for p in parsed[:-1] if p[0] != 0] if len(parsed) >= 2 else parsed
        if not cand:
            last_is_txn = False
            continue
        value, hint = abs(cand[-1][0]), cand[-1][1]
        direction = hint
        if direction is None and bal is not None and prev_bal is not None:
            delta = round(bal - prev_bal, 2)
            if abs(abs(delta) - value) < 0.011:
                direction = "credit" if delta > 0 else "debit"
        if direction is None:
            direction = "credit" if _CREDIT_WORDS.search(rest) else "debit"
        if bal is not None:
            prev_bal = bal
        amt = f"{value:.2f}"
        rows.append([d, rest.strip(), amt if direction == "debit" else "", amt if direction == "credit" else "",
                     f"{bal:.2f}" if bal is not None else "", ""])
        last_is_txn = True
    return rows


# ─── Column-position parser ─────────────────────────────────────────────────
# Header words -> column role. Matched against single words (upper-cased, punctuation stripped).
_HEAD_ROLES = {
    "date": {"DATE", "TXN", "TRAN", "TRANSACTION"},
    "desc": {"PARTICULARS", "DESCRIPTION", "NARRATION", "REMARKS", "DETAILS"},
    "debit": {"WITHDRAWALS", "WITHDRAWAL", "DEBIT", "DEBITS", "DR"},
    "credit": {"DEPOSITS", "DEPOSIT", "CREDIT", "CREDITS", "CR"},
    "balance": {"BALANCE"},
}
_FOOTER_LINE = re.compile(r"^(total\b|grand total|page \d+ of \d+|closing balance|statement summary|"
                          r"account related|legends? for|\*\s)", re.I)
_OPENING_DESC = re.compile(r"^(b/f|opening balance|balance b/f|brought forward)\b", re.I)
_NUM = re.compile(r"^\(?-?(?:Rs\.?|₹)?\d[\d,]*\.\d{2}\)?(?:Cr|Dr|CR|DR)?$")


def _find_header(words: list) -> Optional[dict]:
    """Return {role: word} for the transaction table header on this page, or None."""
    by_line: dict = {}
    for w in words:
        by_line.setdefault(round(w["top"]), []).append(w)
    for top in sorted(by_line):
        line = [w for t in (top - 1, top, top + 1) for w in by_line.get(t, [])]
        found: dict = {}
        for w in sorted(line, key=lambda w: w["x0"]):
            word = re.sub(r"[^A-Z]", "", w["text"].upper())
            for role, names in _HEAD_ROLES.items():
                if word in names and role not in found:  # leftmost wins (txn date before value date)
                    found[role] = w
        if {"date", "desc", "balance"} <= found.keys() and ({"debit", "credit"} & found.keys()):
            return found
    return None


def _positional_rows(page) -> Rows:
    """Rebuild transactions from word coordinates: one row per date in the DATE column; narration words
    above/below a date line belong to the nearest date; amounts go to the nearest amount column."""
    try:
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    except Exception:
        return []
    head = _find_header(words)
    if not head:
        return []
    head_bottom = max(w["bottom"] for w in head.values())
    date_x1 = head["desc"]["x0"] - 2  # date column ends where particulars begin
    amount_cols = {r: head[r]["x1"] for r in ("debit", "credit", "balance") if r in head}
    first_amount_x0 = min(head[r]["x0"] for r in amount_cols) - 15

    body = [w for w in words if w["top"] > head_bottom + 1]
    # Stop at the table footer ("Total:", "Page 2 of 7", ...): a LINE (not just any word) starting with it.
    lines: dict = {}
    for w in body:
        lines.setdefault(round(w["top"]), []).append(w)
    stop_y = float("inf")
    for top in sorted(lines):
        text = " ".join(w["text"] for w in sorted(lines[top], key=lambda w: w["x0"]))
        if _FOOTER_LINE.match(text):
            stop_y = top
            break
    # Drop "Opening Balance ..." / "B/F" info lines so their words don't leak into a transaction.
    skip_tops = {top for top in lines
                 if _OPENING_DESC.match(" ".join(w["text"] for w in sorted(lines[top], key=lambda w: w["x0"])))}
    body = [w for w in body if w["top"] < stop_y - 1 and round(w["top"]) not in skip_tops]

    anchors = sorted((w for w in body if w["x1"] <= date_x1 + 20 and w["x0"] < date_x1 and parse_date(w["text"])),
                     key=lambda w: w["top"])
    if not anchors:
        return []
    groups: List[dict] = [{"date": parse_date(a["text"]), "top": a["top"], "desc": [], "amt": {}} for a in anchors]
    anchor_ids = {id(a) for a in anchors}

    # If the PDF draws row separator lines, use them: a word belongs to the row whose band contains it.
    try:
        # Separators are often drawn as one short segment per column, so sum segment widths per y.
        spans: dict = {}
        for l in page.lines:
            if abs(l["top"] - l["bottom"]) < 1:
                y = round(l["top"], 1)
                spans[y] = spans.get(y, 0.0) + (l["x1"] - l["x0"])
        rules = sorted(y for y, width in spans.items() if width > page.width * 0.4)
    except Exception:
        rules = []

    def band(y: float) -> Optional[int]:
        if len(rules) < 2:
            return None
        for i in range(len(rules) - 1):
            if rules[i] <= y < rules[i + 1]:
                return i
        return None

    band_group = {}
    for g, a in zip(groups, anchors):
        b = band((a["top"] + a["bottom"]) / 2)
        if b is not None:
            band_group.setdefault(b, g)

    for w in body:
        if id(w) in anchor_ids:
            continue
        # Use the word's vertical middle: its top can sit a fraction above the row's separator line.
        b = band((w["top"] + w["bottom"]) / 2)
        g = band_group.get(b) if b is not None else None
        if g is None and band_group:
            g = min(groups, key=lambda g: abs(g["top"] - w["top"]))
        if g is None:
            # No row separators: wrapped narration continues BELOW its date line, so a word belongs to
            # the last date line at or above it.
            above = [g for g in groups if g["top"] <= w["top"] + 2]
            g = above[-1] if above else groups[0]
        if w["x0"] >= first_amount_x0 and _NUM.match(w["text"]):
            role = min(amount_cols, key=lambda r: abs(amount_cols[r] - w["x1"]))
            g["amt"].setdefault(role, w["text"])
        elif w["x0"] >= head["desc"]["x0"] - 2 or w["x0"] > date_x1:
            g["desc"].append(w)
        # words in the MODE column (between date and particulars) are ignored on purpose

    rows: Rows = []
    for g in groups:
        desc = " ".join(w["text"] for w in sorted(g["desc"], key=lambda w: (round(w["top"]), w["x0"])))
        debit, credit, bal = g["amt"].get("debit", ""), g["amt"].get("credit", ""), g["amt"].get("balance", "")
        if not debit and not credit:
            # Opening balance (B/F) or an info line: not a transaction.
            continue
        if _OPENING_DESC.match(desc):
            continue
        clean = lambda s: s.replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
        rows.append([g["date"], desc.strip(), clean(debit), clean(credit), clean(bal), ""])
    return rows
