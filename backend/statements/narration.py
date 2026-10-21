"""Parse Indian bank / UPI narrations into channel, counterparty, VPA and UTR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

VPA_RE = re.compile(r"\b([a-z0-9][a-z0-9._]{1,63}@[a-z][a-z0-9]{1,31})\b", re.I)
UTR_RE = re.compile(r"(?<![\dA-Za-z])(\d{12})(?![\dA-Za-z])")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$", re.I)
_APP_PHRASE_RE = re.compile(r"^\s*(paid to|received from|transfer to|transferred to|money sent to|sent to|"
                            r"payment to|payment from)\s+(.+?)\s*$", re.I)
_CHANNELS = [("UPI", r"^upi\b"), ("NEFT", r"^neft\b"), ("IMPS", r"^(imps|mmt)\b"), ("RTGS", r"^rtgs\b"),
             ("POS", r"^(pos|pcd|ecom)\b"), ("ATM", r"^(atm|atw|nwd|awb|cash wdl)\b"), ("ACH", r"^(ach|nach)\b")]
_STOP = {"upi", "dr", "cr", "p2m", "p2a", "payment", "paym", "pay", "fr", "from", "to", "via", "na", "ref",
         "txn", "neft", "imps", "rtgs", "pos", "ach", "nach", "ecom", "purchase", "debit", "credit", "collect",
         "request", "intent", "inb", "mb", "ib", "bil", "onl", "mmt", "atw", "nwd", "atm", "wdl", "sent", "using",
         "upiintent", "upi intent", "transfer", "trf", "rev"}
_BANK_CODES = {"yesb", "utib", "sbin", "hdfc", "icic", "kkbk", "barb", "punb", "cnrb", "idib", "ubin", "ioba",
               "indb", "fdrl", "airp", "paytm", "ptm", "ybl", "okaxis", "oksbi", "okhdfcbank", "okicici", "ibl", "axl",
               "sbi", "axis", "icici", "kotak"}
BUSINESS_WORDS = {"ltd", "limited", "pvt", "private", "llp", "inc", "co", "company", "corp", "corporation",
                  "retail", "store", "stores", "mart", "supermart", "supermarket", "foods", "food", "cafe", "bistro",
                  "restaurant", "hotel", "kitchen", "bakery", "pharmacy", "medical", "medicals", "chemist",
                  "enterprises", "enterprise", "traders", "trading", "services", "service", "solutions",
                  "technologies", "tech", "hub", "digital", "india", "bank", "electronics", "mobile", "mobiles",
                  "motors", "fuel", "fuels", "petroleum", "petrol", "station", "shop", "centre", "center", "agency",
                  "general", "industries", "labs", "studio", "club", "salon", "clinic", "hospital", "online",
                  "payments", "networks", "telecom", "broadband", "insurance", "finance", "travels", "tours",
                  "fashion", "garments", "jewellers", "hardware", "pay", "wallet", "recharge", "bills"}


@dataclass
class Narration:
    channel: Optional[str]
    counterparty: Optional[str]
    vpa: Optional[str]
    utr: Optional[str]
    is_person: bool
    p2m: bool = False


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def _clean_phrase(phrase: str) -> str:
    words = []
    for w in re.split(r"\s+", phrase.strip()):
        lw = re.sub(r"[^a-z0-9]", "", w.lower())
        if not lw or lw in _STOP or lw in _BANK_CODES:
            continue
        if sum(ch.isdigit() for ch in lw) >= 2 or ("x" in lw and any(ch.isdigit() for ch in lw)):
            continue  # reference numbers, masked cards / accounts
        if IFSC_RE.match(w.strip(".,:;")):
            continue
        words.append(w.strip(".,:;*"))
    return " ".join(w for w in words if w)


def _is_person_name(name: str) -> bool:
    words = name.split()
    if not 2 <= len(words) <= 3:
        return False
    if not all(re.fullmatch(r"[A-Za-z.]+", w) for w in words):
        return False
    return not any(w.lower().strip(".") in BUSINESS_WORDS for w in words)


def parse_narration(desc: str) -> Narration:
    text = (desc or "").strip()
    low = text.lower()
    vpa_m = VPA_RE.search(text)
    vpa = vpa_m.group(1).lower() if vpa_m else None
    utr_m = UTR_RE.search(text)
    utr = utr_m.group(1) if utr_m else None

    channel = None
    for name, pat in _CHANNELS:
        if re.search(pat, low):
            channel = name
            break
    if channel is None and re.search(r"\bupi/", low):
        channel = "UPI"  # e.g. ICICI puts the payee name before "UPI/..."
    app = _APP_PHRASE_RE.match(text)
    if app:
        channel = channel or "UPI"
        counterparty = _clean_phrase(app.group(2)) or None
    else:
        if vpa and not channel:
            channel = "UPI"
        body = text.replace(vpa_m.group(1), " ") if vpa_m else text
        counterparty = None
        for token in re.split(r"[/|\-]+", body):
            phrase = _clean_phrase(token)
            if len(re.sub(r"[^A-Za-z]", "", phrase)) >= 3 and not phrase.lower().endswith(" bank"):
                counterparty = phrase
                break
    if counterparty:
        counterparty = re.sub(r"\s+", " ", counterparty).strip()[:80].upper()
    tokens = set(re.split(r"[^a-z0-9]+", low))
    p2m = "p2m" in tokens
    if channel is None and (p2m or "p2a" in tokens):
        channel = "UPI"
    phone_vpa = bool(vpa and re.fullmatch(r"\+?\d{10,12}", vpa.split("@")[0]))
    is_person = not p2m and ("p2a" in tokens or phone_vpa or bool(counterparty and _is_person_name(counterparty)))
    if channel not in ("UPI", "IMPS", "NEFT", "RTGS"):
        is_person = False
    return Narration(channel, counterparty, vpa, utr, is_person, p2m)


def payee_key(n: Narration, desc: str) -> str:
    if n.vpa:
        return n.vpa.lower()
    if n.counterparty:
        return "name:" + normalize(n.counterparty)
    return "desc:" + " ".join(normalize(desc).split()[:3])


def display_merchant(n: Narration, desc: str) -> str:
    if n.counterparty:
        return n.counterparty.title()
    if n.vpa:
        return n.vpa.split("@")[0]
    return " ".join((desc or "").split()[:4])[:80]
