"""Transaction type + category: type rules -> payee memory -> curated merchant rules -> one LLM batch -> Other."""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from backend import config, llm
from backend.statements.narration import normalize
from categorizer import CANONICAL_CATEGORIES

log = logging.getLogger(__name__)

REVIEW_BELOW = 0.75
AI_CONF_CAP = 0.8
AI_BATCH = 40
_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "data", "statement_rules.json")
_GATEWAY = re.compile(r"\b(airtel pay|airtel|payu|razorpay|pay via ra|rzp|bharatpe|paytmqr|paytm|phonepe|"
                      r"yes bank|yesbank|state bank|hdfc bank|axis bank|icici|citibank|federal|bank of|"
                      r"punjab nat|upi|upiintent|mandateexe|verified)\b")
_DEBIT_ONLY = {"card_bill_payment", "cash_withdrawal", "investment"}
_CREDIT_ONLY = {"refund", "income"}


def _rx(patterns: List[str]) -> Optional[re.Pattern]:
    pats = [r"\b" + re.escape(normalize(p)) + r"\b" for p in patterns if normalize(p)]
    return re.compile("|".join(pats)) if pats else None


@lru_cache(maxsize=1)
def rules() -> dict:
    with open(_RULES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    merchants = []
    for m in raw["merchants"]:
        if m["category"] not in CANONICAL_CATEGORIES:
            raise ValueError(f"bad category in statement_rules.json: {m['category']}")
        merchants.append({**m, "rx": _rx(m["patterns"]), "vpa": [v.lower() for v in m.get("vpa", [])]})
    # Generic shop-type words ("cafe", "snacks", "salon"...) for local merchants that no curated rule knows.
    # Checked after curated merchants; they keep the payee's own name (name=None).
    for k in raw.get("keyword_rules", []):
        if k["category"] not in CANONICAL_CATEGORIES:
            raise ValueError(f"bad category in statement_rules.json: {k['category']}")
        merchants.append({"name": None, "category": k["category"], "confidence": k.get("confidence", 0.8),
                          "rx": _rx(k["keywords"]), "vpa": []})
    return {"merchants": merchants,
            "types": [{"type": t["type"], "rx": _rx(t["patterns"])} for t in raw["type_rules"]],
            "self": _rx(raw["self_transfer_patterns"])}


def match_merchant(text: str, vpa: Optional[str]) -> Optional[dict]:
    local = (vpa or "").split("@")[0]
    for m in rules()["merchants"]:
        if local and any(v in local for v in m["vpa"]):
            return m
        if m["rx"] and m["rx"].search(text):
            return m
    return None


def _set(t: dict, txn_type: str, category: str, source: str, conf: float, merchant: Optional[str] = None) -> None:
    t.update(txn_type=txn_type, category=category, category_source=source, confidence=round(conf, 2))
    if merchant:
        t["merchant"] = merchant


def categorize(txns: List[dict], memory: Dict[str, dict], use_ai: bool = True) -> None:
    """Fill txn_type / category / category_source / confidence / needs_review in place."""
    pending: List[dict] = []
    for t in txns:
        text = normalize(" ".join(filter(None, [t["description"], t.get("counterparty"), t.get("vpa")])))
        debit = t["direction"] == "debit"
        mem = memory.get(t.get("payee_key") or "")
        if debit and mem:
            _set(t, mem.get("txn_type") or "expense", mem["category"], "memory", 0.95)
            continue
        ttype = None
        for rule in rules()["types"]:
            allowed = (rule["type"] not in _DEBIT_ONLY or debit) and (rule["type"] not in _CREDIT_ONLY or not debit)
            if allowed and rule["rx"] and rule["rx"].search(text):
                ttype = rule["type"]
                break
        if ttype is None and rules()["self"].search(text):
            ttype = "transfer_self"
        if ttype:
            _set(t, ttype, "Other", "rule", 0.9)
            continue
        if not debit:
            _set(t, "income", "Other", "rule", 0.8)
            continue
        # Match the payee's own name first; the full narration also contains payment-gateway and bank names
        # ("AIRTEL PAY", "YES BANK", "Pay via Razorpay") that would otherwise pick the wrong merchant.
        name_text = normalize(" ".join(filter(None, [t.get("counterparty"), t.get("merchant")])))
        m = match_merchant(name_text, t.get("vpa")) if name_text else None
        if m is None and not t.get("is_person"):
            m = match_merchant(_GATEWAY.sub(" ", text), None)
        if m:
            _set(t, "expense", m["category"], "rule", m.get("confidence", 0.9), m["name"])
            continue
        if t.get("is_person"):
            _set(t, "transfer_person", "Other", "rule", 0.6)
            continue
        _set(t, "expense", "Other", "none", 0.0)
        pending.append(t)
    if pending and use_ai and llm.is_available(config.ollama_text_model()):
        for i in range(0, len(pending), AI_BATCH):
            _ai_batch(pending[i:i + AI_BATCH])
    for t in txns:
        t["needs_review"] = int(t["txn_type"] == "transfer_person" or t["confidence"] < REVIEW_BELOW or
                                (t["txn_type"] == "expense" and t["category"] == "Other"))


class _AIItem(BaseModel):
    i: int
    category: str
    confidence: float = Field(ge=0, le=1)


class _AIOut(BaseModel):
    items: List[_AIItem]


def _ai_batch(batch: List[dict]) -> None:
    lines = "\n".join(f"{i}. {t['description'][:160]} | {t['amount']:.2f}" for i, t in enumerate(batch))
    prompt = ("Categorize each Indian bank/UPI payment into exactly one category from: "
              f"{', '.join(CANONICAL_CATEGORIES)}. Use 'Other' when unsure. Return one item per line index "
              f"with a confidence 0-1.\n\n{lines}")
    try:
        content = llm.chat(config.ollama_text_model(), [{"role": "user", "content": prompt}],
                           schema=_AIOut.model_json_schema())
        out = _AIOut.model_validate(json.loads(content))
    except (llm.LLMError, ValidationError, json.JSONDecodeError, TypeError) as exc:
        log.warning("AI statement categorization failed: %s", exc)
        return
    for item in out.items:
        if 0 <= item.i < len(batch) and item.category in CANONICAL_CATEGORIES and item.category != "Other":
            _set(batch[item.i], "expense", item.category, "ai", min(item.confidence, AI_CONF_CAP))
