"""AI insights (cached) and tool-calling chat over the user's own spending data, with rule-based fallbacks."""

from __future__ import annotations

import json
import logging
import re
from datetime import date as date_cls
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from backend import analytics, config, db, llm

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


class InsightPayload(BaseModel):
    summary: str
    tips: List[str] = Field(default_factory=list)


def _fmt(amount: float, currency: str) -> str:
    return f"{_SYMBOLS.get(currency, currency + ' ')}{amount:,.2f}"


def _anomalies(snap: dict) -> List[str]:
    cur = snap["currency"]
    out = []
    for b in snap["budget_status"]:
        if b["status"] == "exceeded":
            out.append(f"{b['category']} is over budget: {_fmt(b['spent'], cur)} of {_fmt(b['limit'], cur)} "
                       f"({b['percentage']:.0f}%).")
    total = snap["total"] or 0
    for it in snap["top_items"]:
        if total > 0 and it["amount"] >= 0.3 * total and snap["receipt_count"] > 1:
            out.append(f"'{it['item_name']}' at {it['store']} ({_fmt(it['amount'], cur)}) is "
                       f"{it['amount'] / total * 100:.0f}% of this month's spend.")
    mom = snap["month_over_month"]
    if mom["change_pct"] is not None and mom["change_pct"] >= 50:
        out.append(f"Spending is up {mom['change_pct']:.0f}% vs {mom['previous_month']}.")
    return out


def _rule_insights(month: str, currency: str, snap: dict) -> dict:
    from monthly_summary import generate_insights
    from storage import get_monthly_data

    data = get_monthly_data(month)
    if data:
        data["currency"] = _SYMBOLS.get(currency, currency)
    lines = generate_insights(data)
    cats = snap["per_category"]
    top = f" Top category: {cats[0]['category']} ({_fmt(cats[0]['amount'], currency)})." if cats else ""
    summary = (f"You spent {_fmt(snap['total'], currency)} across {snap['receipt_count']} receipt(s) in {month}.{top}")
    return {"summary": summary, "tips": lines[:6]}


def _ai_insights(snap: dict) -> dict:
    prompt = (
        "You are a careful personal finance assistant. Below are pre-computed spending numbers for one month "
        "(JSON). Write a short summary (2-3 sentences) and 3-5 specific, actionable tips to save money. "
        "Use only the numbers provided; do not invent or recompute totals. Refer to amounts with the currency "
        f"{snap['currency']}. No emojis.\n\nDATA:\n{json.dumps(snap, default=str)}"
    )
    content = llm.chat(config.ollama_text_model(), [{"role": "user", "content": prompt}],
                       schema=InsightPayload.model_json_schema())
    payload = InsightPayload.model_validate(json.loads(content))
    return {"summary": payload.summary.strip(), "tips": [t.strip() for t in payload.tips if t.strip()][:5]}


def get_insights(month: Optional[str] = None, currency: Optional[str] = None, refresh: bool = False) -> dict:
    month = month or date_cls.today().strftime("%Y-%m")
    currency = currency or analytics.dominant_currency()
    snap = analytics.month_snapshot(month, currency)
    base = {"month": month, "currency": currency}
    if snap["receipt_count"] == 0:
        return {**base, "summary": f"No receipts recorded for {month} yet. Scan a receipt to get insights.",
                "tips": [], "anomalies": [], "source": "rules"}

    fingerprint = analytics.receipt_fingerprint(month, currency)
    if not refresh:
        cached = db.get_cached_insights(month, currency, fingerprint)
        if cached:
            return cached

    anomalies = _anomalies(snap)
    if llm.is_available(config.ollama_text_model()):
        try:
            result = {**base, **_ai_insights(snap), "anomalies": anomalies, "source": "ai"}
            db.set_cached_insights(month, currency, fingerprint, result)
            return result
        except (llm.LLMError, ValidationError, json.JSONDecodeError) as exc:
            log.warning("AI insights failed, using rules: %s", exc)
    return {**base, **_rule_insights(month, currency, snap), "anomalies": anomalies, "source": "rules"}


# ─── Chat tools ─────────────────────────────────────────────────────────────
class ToolArgError(ValueError):
    pass


def _iso_date(v: Any, name: str, required: bool = True) -> Optional[str]:
    if v in (None, ""):
        if required:
            raise ToolArgError(f"{name} is required (YYYY-MM-DD)")
        return None
    try:
        return date_cls.fromisoformat(str(v)).isoformat()
    except ValueError as exc:
        raise ToolArgError(f"{name} must be YYYY-MM-DD") from exc


def _category(v: Any) -> Optional[str]:
    from categorizer import CANONICAL_CATEGORIES

    if v in (None, ""):
        return None
    if v not in CANONICAL_CATEGORIES:
        raise ToolArgError(f"category must be one of {CANONICAL_CATEGORIES}")
    return v


def _int(v: Any, name: str, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v) if v not in (None, "") else default
    except (TypeError, ValueError) as exc:
        raise ToolArgError(f"{name} must be an integer") from exc
    if not lo <= n <= hi:
        raise ToolArgError(f"{name} must be between {lo} and {hi}")
    return n


def _month(v: Any) -> str:
    s = str(v or date_cls.today().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s):
        raise ToolArgError("month must be YYYY-MM")
    return s


def _query(v: Any) -> str:
    q = str(v or "").strip()[:100]
    if not q:
        raise ToolArgError("query is required")
    return q


def _tools(currency: str) -> Dict[str, Callable[[dict], Any]]:
    return {
        "get_total_spend": lambda a: analytics.total_spend(
            _iso_date(a.get("from"), "from"), _iso_date(a.get("to"), "to"), currency, _category(a.get("category"))),
        "get_category_breakdown": lambda a: analytics.category_breakdown(
            _iso_date(a.get("from"), "from"), _iso_date(a.get("to"), "to"), currency),
        "get_merchant_breakdown": lambda a: analytics.merchant_breakdown(
            _iso_date(a.get("from"), "from"), _iso_date(a.get("to"), "to"), currency,
            _int(a.get("limit"), "limit", 10, 1, 50), _category(a.get("category"))),
        "get_monthly_trend": lambda a: analytics.monthly_trend(
            currency=currency, months=_int(a.get("months"), "months", 6, 1, 24)),
        "search_items": lambda a: analytics.search_items(
            _query(a.get("query")),
            _iso_date(a.get("from"), "from", False), _iso_date(a.get("to"), "to", False), currency),
        "get_budget_status": lambda a: analytics.budget_status(_month(a.get("month")), currency),
    }


def _fn(name: str, desc: str, props: dict, required: List[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


_D = {"type": "string", "description": "ISO date YYYY-MM-DD"}
TOOL_SPECS = [
    _fn("get_total_spend", "Total spend between two dates, optionally for one category.",
        {"from": _D, "to": _D, "category": {"type": "string"}}, ["from", "to"]),
    _fn("get_category_breakdown", "Spend per category between two dates.", {"from": _D, "to": _D}, ["from", "to"]),
    _fn("get_merchant_breakdown", "Top merchants by spend between two dates, optionally only for one category.",
        {"from": _D, "to": _D, "limit": {"type": "integer"}, "category": {"type": "string"}}, ["from", "to"]),
    _fn("get_monthly_trend", "Total spend per month for the last N months.", {"months": {"type": "integer"}}, []),
    _fn("search_items", "Find purchased line items whose name contains the query.",
        {"query": {"type": "string"}, "from": _D, "to": _D}, ["query"]),
    _fn("get_budget_status", "Budget limits vs spend per category for a month.",
        {"month": {"type": "string", "description": "YYYY-MM"}}, []),
]


def run_tool(name: str, args: dict, currency: str) -> Any:
    tools = _tools(currency)
    if name not in tools:
        return {"error": f"unknown tool {name}"}
    try:
        return tools[name](args if isinstance(args, dict) else {})
    except ToolArgError as exc:
        return {"error": str(exc)}


def chat(message: str, history: Optional[List[dict]] = None, currency: Optional[str] = None) -> dict:
    currency = currency or analytics.dominant_currency()
    if not llm.is_available(config.ollama_text_model()):
        from monthly_summary import query_financial_advisor
        return {"answer": query_financial_advisor(message), "tool_calls": [], "source": "rules"}

    from categorizer import CANONICAL_CATEGORIES
    system = (
        f"You are a personal finance assistant for one user. Today is {date_cls.today().isoformat()}. "
        f"Amounts are in {currency}. Categories: {', '.join(CANONICAL_CATEGORIES)}. "
        "Always call the provided tools to get numbers; never guess figures. Answer concisely in plain language, "
        "give practical advice when asked, and say so when there is no data. No emojis."
    )
    messages: List[dict] = [{"role": "system", "content": system}]
    for h in (history or [])[-10:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            messages.append({"role": h["role"], "content": h["content"][:4000]})
    messages.append({"role": "user", "content": message})

    calls_made: List[dict] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            reply = llm.chat_tools(config.ollama_text_model(), messages, TOOL_SPECS)
            tool_calls = reply.get("tool_calls") or []
            if not tool_calls:
                return {"answer": reply.get("content", ""), "tool_calls": calls_made,
                        "source": "ai"}
            messages.append({"role": "assistant", "content": reply.get("content", ""), "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                result = run_tool(name, args, currency)
                calls_made.append({"name": name, "arguments": args})
                messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)})
        final = llm.chat(config.ollama_text_model(), messages)
        return {"answer": final, "tool_calls": calls_made, "source": "ai"}
    except llm.LLMError as exc:
        log.warning("AI chat failed, using rules: %s", exc)
        from monthly_summary import query_financial_advisor
        return {"answer": query_financial_advisor(message), "tool_calls": calls_made, "source": "rules"}
