"""Statement import: narration parsing, value parsing, column detection, PDFs and categorization tiers."""
import json
import os

import pytest

from backend import llm
from backend.statements import categorize, parsing
from backend.statements.narration import parse_narration, payee_key
from backend.statements.parsing import StatementError
from backend.statements.pdf_text import read_pdf

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_statements")


@pytest.mark.parametrize("desc,channel,cp,vpa,utr,person", [
    ("UPI-SWIGGY-swiggy@axisbank-UTIB0000123-629012345678-Payment", "UPI", "SWIGGY", "swiggy@axisbank",
     "629012345678", False),  # HDFC
    ("UPI/DR/629012345678/SWIGGY/YESB/swiggy@ybl/Payment", "UPI", "SWIGGY", "swiggy@ybl", "629012345678", False),  # SBI
    ("UPI/629012345678/SWIGGY/swiggy@ybl/Payment fr/YES BANK", "UPI", "SWIGGY", "swiggy@ybl", "629012345678", False),
    ("UPI/P2M/629012345678/SWIGGY LIMITED/Payment/YES BANK", "UPI", "SWIGGY LIMITED", None, "629012345678", False),
    ("UPI/RAHUL SHARMA/629012345678/Dinner", "UPI", "RAHUL SHARMA", None, "629012345678", True),  # Kotak
    ("NEFT CR-HDFC0000123-ACME TECHNOLOGIES PVT LTD-SALARY OCT", "NEFT", "ACME TECHNOLOGIES PVT LTD", None, None, False),
    ("IMPS-629012345678-RAHUL SHARMA-SBIN-XXXX1234-rent", "IMPS", "RAHUL SHARMA", None, "629012345678", True),
    ("POS 416021XXXXXX1234 SUPERMART RETAIL", "POS", "SUPERMART RETAIL", None, None, False),
    ("ATW-416021XXXXXX1234-S1AW0123-BANGALORE", "ATM", "BANGALORE", None, None, False),
    ("Paid to Swiggy", "UPI", "SWIGGY", None, None, False),  # PhonePe
    ("Received from Rahul Sharma", "UPI", "RAHUL SHARMA", None, None, True),
    ("UPI-PRIYA K-9876543210@ybl-SBIN0001234-629012345679-rent", "UPI", "PRIYA K", "9876543210@ybl", "629012345679", True),
])
def test_narrations(desc, channel, cp, vpa, utr, person):
    n = parse_narration(desc)
    assert (n.channel, n.counterparty, n.vpa, n.utr, n.is_person) == (channel, cp, vpa, utr, person)
    assert payee_key(n, desc)


def test_dates_and_amounts():
    for s in ("18/10/2026", "18-10-26", "18.10.2026", "18 Oct 2026", "18-Oct-26", "2026-10-18", "Oct 18, 2026",
              "18/10/2026 10:32:00"):
        assert parsing.parse_date(s) == "2026-10-18", s
    assert parsing.parse_date("10/18/2026", "mdy") == "2026-10-18"
    assert parsing.parse_date("31/02/2026") is None
    assert parsing.parse_amount("₹1,23,456.50") == (123456.5, None)
    assert parsing.parse_amount("Rs. 465.36 Dr") == (465.36, "debit")
    assert parsing.parse_amount("(40.00)") == (-40.0, None)
    assert parsing.parse_amount("-1,000") == (-1000.0, None)
    assert parsing.parse_amount("abc") is None and parsing.parse_amount("") is None


def test_single_amount_drcr_csv():
    rows = parsing.read_csv(b"Txn Date,Description,Amount,Dr/Cr,Balance\n01/10/2026,UPI-ZOMATO-zomato@hdfcbank-"
                            b"629000000001,250.00,DR,750.00\n02/10/2026,SALARY,1000.00,CR,1750.00\n")
    m, conf = parsing.detect_mapping(rows)
    assert conf == 1.0 and m["amount"] == 2 and m["drcr"] == 3
    txns, skipped = parsing.apply_mapping(rows, m)
    assert [(t["direction"], t["amount"]) for t in txns] == [("debit", 250.0), ("credit", 1000.0)] and skipped == 0


def test_headerless_needs_manual_mapping():
    rows = parsing.read_csv(b"01/10/2026,Swiggy order,250.00,\n02/10/2026,more text,,99.00\n")
    m, conf = parsing.detect_mapping(rows)
    assert m is None and conf == 0
    mapping = {"header_row": -1, "date": 0, "description": 1, "debit": 2, "credit": 3, "amount": None,
               "drcr": None, "balance": None, "reference": None, "date_order": "dmy"}
    parsing.validate_mapping(mapping, 4, len(rows))
    txns, _ = parsing.apply_mapping(rows, mapping)
    assert [(t["direction"], t["amount"]) for t in txns] == [("debit", 250.0), ("credit", 99.0)]
    with pytest.raises(StatementError):
        parsing.validate_mapping({**mapping, "credit": 2}, 4, len(rows))


def test_sniff_rejects_binary_and_empty():
    with pytest.raises(StatementError):
        parsing.sniff(b"")
    with pytest.raises(StatementError):
        parsing.sniff(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert parsing.sniff(b"PK\x03\x04....xl/workbook.xml") == "xlsx"


def test_text_pdf_bank_and_upi_layouts():
    rows, text = read_pdf(open(os.path.join(SAMPLES, "icici_aug_oct_2026.pdf"), "rb").read())
    m, conf = parsing.detect_mapping(rows)
    txns, _ = parsing.apply_mapping(rows, m)
    assert conf == 1.0 and len(txns) == 11 and parsing.detect_source(text) == ("icici", "XX5678")
    assert txns[0]["description"].endswith("Groceries")  # continuation line merged
    assert [t["direction"] for t in txns if t["amount"] in (10000.0, 1299.0)] == ["credit", "credit"]
    rows, text = read_pdf(open(os.path.join(SAMPLES, "phonepe_oct_2026.pdf"), "rb").read())
    txns, _ = parsing.apply_mapping(rows, parsing.detect_mapping(rows)[0])
    assert len(txns) == 5 and txns[3]["reference"] == "629017100001" and txns[2]["direction"] == "credit"


def test_password_pdf_codes():
    data = open(os.path.join(SAMPLES, "icici_aug_oct_2026_protected.pdf"), "rb").read()
    with pytest.raises(StatementError) as e:
        read_pdf(data)
    assert e.value.code == "password_required" and e.value.status == 422
    with pytest.raises(StatementError) as e:
        read_pdf(data, "wrong")
    assert e.value.code == "password_incorrect"
    rows, _ = read_pdf(data, "ICICI5678")
    assert len(rows) == 12


def test_image_only_pdf_rejected():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.rect(10, 10, 50, 50, style="F")
    with pytest.raises(StatementError, match="scanned"):
        read_pdf(bytes(pdf.output()))


def _t(desc, direction="debit", amount=100.0, **kw):
    from backend.statements.narration import display_merchant
    n = parse_narration(desc)
    return {"description": desc, "direction": direction, "amount": amount, "counterparty": n.counterparty,
            "vpa": n.vpa, "is_person": n.is_person, "payee_key": payee_key(n, desc),
            "merchant": display_merchant(n, desc), **kw}


def test_categorization_order_memory_rules_ai(monkeypatch):
    swiggy = _t("UPI-SWIGGY-swiggy@axisbank-UTIB0000123-629012345678-Payment")
    swiggy_mem = _t("UPI-SWIGGY-swiggy@axisbank-UTIB0000123-629012345679-Payment")
    unknown = _t("UPI-SRI LAKSHMI GENERAL STORES-srilakshmi@okaxis-629012345670-Payment")
    salary = _t("NEFT CR-CITI0000002-ACME TECHNOLOGIES PVT LTD-SALARY", "credit")
    atm = _t("ATW-416021XXXXXX1234-S1AW0123-BANGALORE")
    person = _t("UPI/RAHUL SHARMA/629012345678/Dinner")
    calls = []

    def fake_chat(model, messages, schema=None):
        calls.append(messages[0]["content"])
        return json.dumps({"items": [{"i": 0, "category": "Household", "confidence": 0.99}]})

    monkeypatch.setattr(llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(llm, "chat", fake_chat)
    memory = {"swiggy@axisbank": {"category": "Food", "txn_type": "expense"}}
    txns = [swiggy, unknown, salary, atm, person]
    categorize.categorize(txns, {}, use_ai=True)
    categorize.categorize([swiggy_mem], memory, use_ai=True)
    assert (swiggy["category"], swiggy["category_source"]) == ("Dining", "rule")
    assert (swiggy_mem["category"], swiggy_mem["category_source"]) == ("Food", "memory")  # memory beats rules
    assert (unknown["category"], unknown["category_source"], unknown["confidence"]) == ("Household", "ai", 0.8)
    assert len(calls) == 1 and "SRI LAKSHMI" in calls[0] and "SWIGGY" not in calls[0]  # only unresolved rows
    assert salary["txn_type"] == "income" and atm["txn_type"] == "cash_withdrawal"
    assert person["txn_type"] == "transfer_person" and person["needs_review"] == 1


def test_categorization_offline_and_bad_ai(monkeypatch):
    t = _t("UPI-SRI LAKSHMI GENERAL STORES-srilakshmi@okaxis-629012345670-Payment")
    categorize.categorize([t], {}, use_ai=True)  # offline (conftest)
    assert (t["category"], t["needs_review"]) == ("Other", 1)
    monkeypatch.setattr(llm, "is_available", lambda *a, **k: True)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "not json")
    t2 = _t("UPI-SRI LAKSHMI GENERAL STORES-srilakshmi@okaxis-629012345671-Payment")
    categorize.categorize([t2], {}, use_ai=True)
    assert (t2["category"], t2["needs_review"]) == ("Other", 1)
