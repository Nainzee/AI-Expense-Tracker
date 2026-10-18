"""
Receipts Database & Financial Analytics Engine.

Features:
- Atomic JSON persistence with file locking and graceful corruption recovery.
- Full CRUD operations: save, get, search, update, delete receipts.
- Multi-currency support ($, €, £, ₹, ¥) in analytics, charts, and reports.
- Date-aware month keying (uses receipt's actual date).
- Multi-dimensional analytics: Category breakdown, Merchant velocity, MoM change.
- Smart AI Financial Advisor insights.
- High-resolution, presentation-grade charts (Donut + Ranked Bar).
- Multi-format data export (CSV and JSON).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "receipts_db", "receipts.json")
CHARTS_DIR = os.path.join(BASE_DIR, "receipts_db")

# ─── Color Palette for Categories ───────────────────────────────────────────
CATEGORY_COLORS: Dict[str, str] = {
    "Food": "#10B981",          # Emerald
    "Drinks": "#3B82F6",        # Blue
    "Dining": "#F97316",        # Orange
    "Bakery": "#EC4899",        # Pink
    "Household": "#F59E0B",     # Amber
    "Personal Care": "#8B5CF6", # Purple
    "Medical": "#EF4444",       # Rose / Red
    "Transportation": "#14B8A6", # Teal
    "Electronics": "#06B6D4",   # Cyan
    "Apparel": "#6366F1",       # Indigo
    "Home & Hardware": "#78716C", # Stone
    "Entertainment": "#A855F7", # Violet
    "Utilities": "#EAB308",     # Gold
    "Office & Stationery": "#0284C7", # Sky Blue
    "Other": "#64748B",         # Slate
}


def get_category_color(cat: str) -> str:
    return CATEGORY_COLORS.get(cat, "#64748B")


# ─── Unified SQLite Storage Delegation ───────────────────────────────────────
from storage import (
    save_receipt,
    get_receipt,
    delete_receipt,
    list_receipts,
    get_available_months,
    get_available_years,
    get_monthly_data,
    get_yearly_data,
    record_user_correction,
    get_user_correction,
)


# ─── Chart Generation (Publication Quality) ──────────────────────────────────
def generate_spending_chart(timeframe: str = "Monthly", period_key: Optional[str] = None) -> Optional[str]:
    """
    Generate modern high-resolution spending charts:
    - Monthly: Donut (Category Share) + Horizontal Ranked Bar chart
    - Weekly: Chronological Week-by-Week Velocity Bar chart + Category Donut
    - Yearly: Month-by-Month Annual Velocity Bar chart + Category Donut
    """
    tf = (timeframe or "Monthly").capitalize()
    if tf == "Yearly":
        data = get_yearly_data(period_key)
    else:
        data = get_monthly_data(period_key)

    if not data or not data.get("category_totals"):
        return None

    curr = data.get("currency", "₹")
    os.makedirs(CHARTS_DIR, exist_ok=True)
    suffix = str(data.get("month") or data.get("year") or "period").replace("/", "_")
    chart_path = os.path.join(CHARTS_DIR, f"chart_{tf}_{suffix}.png")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), dpi=150)
    fig.patch.set_facecolor("#0F172A")

    cats = list(data["category_totals"].keys())
    amts = [max(0.0, data["category_totals"][c]) for c in cats]
    colors = [get_category_color(c) for c in cats]

    if tf == "Weekly":
        # ── Panel 1: Weekly Breakdown Bar Chart ──
        ax1 = axes[0]
        ax1.set_facecolor("#0F172A")
        week_keys = [f"Week {i}" for i in range(1, 6)]
        week_vals = [data.get("weekly", {}).get(wk, 0.0) for wk in week_keys]

        bars = ax1.bar(week_keys, week_vals, color="#3B82F6", edgecolor="none", width=0.55)
        max_w = max(week_vals) if (week_vals and max(week_vals) > 0) else 100
        for bar, val in zip(bars, week_vals):
            if val > 0:
                ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max_w * 0.02,
                         f"{curr}{val:,.0f}", ha="center", va="bottom", fontsize=9.5, fontweight="bold", color="#F8FAFC")

        ax1.set_title(f"Weekly Spend Velocity ({data.get('month', '')})", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")
        ax1.set_ylabel(f"Outlay ({curr})", fontsize=10, color="#94A3B8")
        ax1.tick_params(axis="x", labelsize=10, colors="#CBD5E1")
        ax1.tick_params(axis="y", labelsize=9, colors="#94A3B8")
        ax1.set_ylim(0, max_w * 1.25)
        ax1.spines[["top", "right"]].set_visible(False)
        ax1.spines["left"].set_color("#334155")
        ax1.spines["bottom"].set_color("#334155")
        ax1.grid(axis="y", color="#1E293B", linestyle="--", alpha=0.7)

        # ── Panel 2: Category Donut Chart ──
        ax2 = axes[1]
        ax2.set_facecolor("#0F172A")
        wedges, texts = ax2.pie(
            amts, colors=colors, startangle=140,
            wedgeprops={"width": 0.48, "edgecolor": "#0F172A", "linewidth": 3}
        )
        ax2.text(0, 0.08, f"{curr}{data['grand_total']:,.2f}", ha="center", va="center",
                 fontsize=17, fontweight="bold", color="#F8FAFC")
        ax2.text(0, -0.15, "TOTAL SPEND", ha="center", va="center",
                 fontsize=9, fontweight="medium", color="#94A3B8")
        legend_labels = [f"{c} ({curr}{v:,.0f})" for c, v in zip(cats, amts)]
        ax2.legend(wedges, legend_labels, loc="lower center", bbox_to_anchor=(0.5, -0.15),
                   ncol=2, fontsize=8.5, frameon=False, labelcolor="#E2E8F0")
        ax2.set_title("Category Distribution", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")

    elif tf == "Yearly":
        # ── Panel 1: Annual Monthly Trend Bar Chart ──
        ax1 = axes[0]
        ax1.set_facecolor("#0F172A")
        trend = data.get("monthly_trend", {})
        sorted_m = sorted(trend.keys())
        m_labels = [m.split("-")[1] if "-" in m else m for m in sorted_m]
        m_vals = [trend[m] for m in sorted_m]

        bars = ax1.bar(m_labels or ["No data"], m_vals or [0], color="#10B981", edgecolor="none", width=0.55)
        max_m = max(m_vals) if (m_vals and max(m_vals) > 0) else 100
        for bar, val in zip(bars, m_vals):
            if val > 0:
                ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max_m * 0.02,
                         f"{curr}{val:,.0f}", ha="center", va="bottom", fontsize=9.5, fontweight="bold", color="#F8FAFC")

        ax1.set_title(f"Annual Monthly Spend ({data.get('year', '')})", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")
        ax1.set_ylabel(f"Outlay ({curr})", fontsize=10, color="#94A3B8")
        ax1.tick_params(axis="x", labelsize=10, colors="#CBD5E1")
        ax1.tick_params(axis="y", labelsize=9, colors="#94A3B8")
        ax1.set_ylim(0, max_m * 1.25)
        ax1.spines[["top", "right"]].set_visible(False)
        ax1.spines["left"].set_color("#334155")
        ax1.spines["bottom"].set_color("#334155")
        ax1.grid(axis="y", color="#1E293B", linestyle="--", alpha=0.7)

        # ── Panel 2: Annual Category Share Donut ──
        ax2 = axes[1]
        ax2.set_facecolor("#0F172A")
        wedges, texts = ax2.pie(
            amts, colors=colors, startangle=140,
            wedgeprops={"width": 0.48, "edgecolor": "#0F172A", "linewidth": 3}
        )
        ax2.text(0, 0.08, f"{curr}{data['grand_total']:,.2f}", ha="center", va="center",
                 fontsize=17, fontweight="bold", color="#F8FAFC")
        ax2.text(0, -0.15, "ANNUAL TOTAL", ha="center", va="center",
                 fontsize=9, fontweight="medium", color="#94A3B8")
        legend_labels = [f"{c} ({curr}{v:,.0f})" for c, v in zip(cats, amts)]
        ax2.legend(wedges, legend_labels, loc="lower center", bbox_to_anchor=(0.5, -0.15),
                   ncol=2, fontsize=8.5, frameon=False, labelcolor="#E2E8F0")
        ax2.set_title("Full-Year Category Allocation", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")

    else:
        # ── Monthly (Default): Donut + Ranked Bars ──
        ax1 = axes[0]
        ax1.set_facecolor("#0F172A")
        wedges, texts = ax1.pie(
            amts, colors=colors, startangle=140,
            wedgeprops={"width": 0.48, "edgecolor": "#0F172A", "linewidth": 3}
        )
        ax1.text(0, 0.08, f"{curr}{data['grand_total']:,.2f}", ha="center", va="center",
                 fontsize=17, fontweight="bold", color="#F8FAFC")
        ax1.text(0, -0.15, "TOTAL SPEND", ha="center", va="center",
                 fontsize=9, fontweight="medium", color="#94A3B8")
        legend_labels = [f"{c} ({curr}{v:,.0f})" for c, v in zip(cats, amts)]
        ax1.legend(wedges, legend_labels, loc="lower center", bbox_to_anchor=(0.5, -0.15),
                   ncol=2, fontsize=8.5, frameon=False, labelcolor="#E2E8F0")
        ax1.set_title("Spending by Category", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")

        ax2 = axes[1]
        ax2.set_facecolor("#0F172A")
        raw_amts = [data["category_totals"][c] for c in cats]
        sorted_pairs = sorted(zip(raw_amts, cats, colors), key=lambda x: x[0])
        s_amts, s_cats, s_colors = zip(*sorted_pairs)
        y_pos = range(len(s_cats))
        bars = ax2.barh(y_pos, s_amts, color=s_colors, height=0.6, edgecolor="none")
        max_amt = max(s_amts) if s_amts else 100
        for bar, amt in zip(bars, s_amts):
            pct = (amt / data["grand_total"]) * 100 if data["grand_total"] > 0 else 0
            ax2.text(bar.get_width() + max_amt * 0.03, bar.get_y() + bar.get_height() / 2,
                     f"{curr}{amt:,.2f} ({pct:.0f}%)", va="center", ha="left", fontsize=9.5, color="#E2E8F0")
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(s_cats, fontsize=10, color="#CBD5E1")
        ax2.set_xlabel(f"Expenditure ({curr})", fontsize=10, color="#94A3B8")
        ax2.set_title("Ranked Category Distribution", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")
        ax2.spines[["top", "right", "left"]].set_visible(False)
        ax2.spines["bottom"].set_color("#334155")
        ax2.tick_params(axis="x", labelsize=9, colors="#94A3B8")
        ax2.tick_params(axis="y", colors="#CBD5E1")
        ax2.set_xlim(0, max_amt * 1.35)
        ax2.grid(axis="x", color="#1E293B", linestyle="--", alpha=0.7)

    plt.tight_layout(pad=2.5)
    plt.savefig(chart_path, dpi=150, bbox_inches="tight", facecolor="#0F172A")
    plt.close()
    return chart_path


def generate_monthly_chart(month_key: Optional[str] = None) -> Optional[str]:
    """Backward compatible wrapper around generate_spending_chart."""
    return generate_spending_chart("Monthly", month_key)


DEFAULT_CATEGORY_BUDGETS: Dict[str, float] = {
    "Food": 15000.0,
    "Drinks": 3000.0,
    "Dining": 5000.0,
    "Bakery": 2000.0,
    "Household": 4000.0,
    "Personal Care": 3500.0,
    "Medical": 3000.0,
    "Transportation": 5000.0,
    "Electronics": 10000.0,
    "Apparel": 5000.0,
    "Home & Hardware": 6000.0,
    "Entertainment": 3000.0,
    "Utilities": 4000.0,
    "Office & Stationery": 2000.0,
    "Other": 2500.0,
}


def calculate_budget_alerts(
    category_totals: Dict[str, float],
    budgets: Optional[Dict[str, float]] = None,
    currency: str = "₹"
) -> List[dict]:
    """Calculate budget limits and alert flags per category."""
    b = budgets or DEFAULT_CATEGORY_BUDGETS
    alerts = []
    for cat, spend in category_totals.items():
        limit = b.get(cat, 5000.0)
        pct = (spend / limit) * 100.0
        if pct >= 100.0:
            status = "🔴 Exceeded"
            msg = f"{cat} exceeded monthly budget by {currency}{spend - limit:,.2f} ({pct:.0f}% utilized)"
        elif pct >= 75.0:
            status = "🟡 Warning"
            msg = f"{cat} approaching budget limit: {pct:.0f}% utilized ({currency}{limit - spend:,.2f} remaining)"
        else:
            status = "🟢 On Track"
            msg = f"{cat} within budget: {pct:.0f}% utilized ({currency}{limit - spend:,.2f} remaining)"
        alerts.append({
            "category": cat,
            "spend": round(spend, 2),
            "budget": round(limit, 2),
            "percentage": round(pct, 1),
            "status": status,
            "message": msg
        })
    return sorted(alerts, key=lambda x: x["percentage"], reverse=True)


def calculate_month_end_forecast(data: dict) -> dict:
    """Compute empirical run-rate forecast to the end of the calendar month."""
    grand = float(data.get("grand_total", 0.0))
    receipts = data.get("receipts", [])
    curr = data.get("currency", "₹")

    days_in_month = 30
    month_str = data.get("month", "")
    if month_str and "-" in month_str:
        try:
            parts = month_str.split("-")
            import calendar
            days_in_month = calendar.monthrange(int(parts[0]), int(parts[1]))[1]
        except Exception:
            days_in_month = 30

    latest_day = 15
    for r in receipts:
        d = r.get("date", "")
        if d and len(d) >= 10:
            try:
                day_num = int(d.split("-")[2][:2])
                if day_num > latest_day:
                    latest_day = day_num
            except Exception:
                pass

    day_progress = max(1, min(latest_day, days_in_month))
    daily_velocity = grand / day_progress
    projected_total = daily_velocity * days_in_month

    return {
        "days_in_month": days_in_month,
        "day_progress": day_progress,
        "daily_velocity": round(daily_velocity, 2),
        "projected_total": round(projected_total, 2),
        "currency": curr
    }


# ─── Smart AI Financial Advisor Insights ─────────────────────────────────────
def generate_insights(data: Optional[dict]) -> List[str]:
    """Generate comprehensive, multi-dimensional financial observations and actionable advice."""
    if not data or not data.get("receipts"):
        return ["No transaction data recorded for this period. Scan or import receipts in Tab 1 to generate AI insights."]

    curr = data.get("currency", "₹")
    receipts = data.get("receipts", [])
    grand = float(data.get("grand_total", 0.0))
    cat_totals = data.get("category_totals", {})
    store_totals = data.get("store_totals", {})
    store_counts = data.get("store_counts", {})
    r_count = len(receipts)

    insights: List[str] = []

    # 0. Active Category Budget Alerts
    budget_alerts = calculate_budget_alerts(cat_totals, currency=curr)
    exceeded = [a for a in budget_alerts if a["status"] == "🔴 Exceeded"]
    warning = [a for a in budget_alerts if a["status"] == "🟡 Warning"]
    if exceeded:
        insights.append(f"Budget Alert: {exceeded[0]['message']}.")
    elif warning:
        insights.append(f"Budget Watch: {warning[0]['message']}.")

    # 0.5. Velocity Forecast / Run-Rate
    if "year" in data and "month" not in data:
        months_active = len(data.get("monthly_trend", {})) or 1
        monthly_avg = grand / max(months_active, 1)
        insights.append(
            f"Annual Run-Rate & Velocity: Outlay across {months_active} active month(s) averages {curr}{monthly_avg:,.2f}/month. "
            f"Projected 12-month annual baseline is ~{curr}{(monthly_avg * 12):,.2f}."
        )
    else:
        forecast = calculate_month_end_forecast(data)
        insights.append(
            f"Month-End Velocity Forecast: Paced at {curr}{forecast['daily_velocity']:,.2f}/day through Day {forecast['day_progress']}. "
            f"Projected month-end outlay is ~{curr}{forecast['projected_total']:,.2f} ({forecast['days_in_month']}-day projection)."
        )


    # 1. Executive Health Score & Discipline Index
    essential_cats = {"Food", "Medical", "Household", "Personal Care", "Utilities", "Transportation"}
    essential_spend = sum(cat_totals.get(c, 0.0) for c in essential_cats)
    essential_ratio = (essential_spend / max(grand, 1.0)) * 100

    if essential_ratio >= 85:
        health_status = "Disciplined (Essentials Focus)"
        health_note = f"{essential_ratio:.1f}% allocated to essentials (groceries, hygiene, healthcare)."
    elif essential_ratio >= 60:
        health_status = "Balanced (Moderate Discretionary)"
        health_note = f"{essential_ratio:.1f}% spent on essential living needs."
    else:
        health_status = "High Discretionary (Lifestyle Velocity)"
        health_note = "Significant proportion spent on dining, apparel, or electronics."

    insights.append(f"Financial Health Score: {health_status} — {health_note}")

    # 2. Primary Expense Driver
    if cat_totals:
        sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
        top_cat, top_amt = sorted_cats[0]
        top_pct = (top_amt / max(grand, 1.0)) * 100
        top_items_count = data.get("category_counts", {}).get(top_cat, 1)
        insights.append(
            f"Primary Expense Driver: {top_cat} accounts for {curr}{top_amt:,.2f} ({top_pct:.1f}% of total spend across {top_items_count} items)."
        )

        # 3. Secondary Category Distribution
        if len(sorted_cats) > 1:
            sec_parts = [f"{c} ({curr}{a:,.2f}, {(a/grand)*100:.1f}%)" for c, a in sorted_cats[1:4]]
            insights.append(f"Secondary Category Allocation: {', '.join(sec_parts)}.")

    # 4. Peak Single-Item Purchase (Basket Spotlight)
    all_items = []
    for r in receipts:
        for it in r.get("items", []):
            all_items.append((it.get("item_name", "Item"), float(it.get("amount", 0.0)), r.get("store", "Store"), r.get("date", "")))

    if all_items:
        all_items.sort(key=lambda x: x[1], reverse=True)
        peak_item = all_items[0]
        insights.append(
            f"Peak Line-Item Expenditure: '{peak_item[0]}' at {curr}{peak_item[1]:,.2f} (from {peak_item[2]} on {peak_item[3]})."
        )

    # 5. Basket Micro-Economics & Trip Velocity
    total_items = len(all_items)
    avg_item_cost = grand / max(total_items, 1)
    insights.append(
        f"Basket Micro-Economics: {total_items} items acquired across {r_count} shopping trips (Avg item cost: {curr}{avg_item_cost:,.2f}; Avg trip basket: {curr}{data.get('avg_per_receipt', 0):,.2f})."
    )

    # 6. Merchant Loyalty Concentration
    if store_totals:
        top_store = max(store_totals, key=store_totals.get)
        top_store_spend = store_totals[top_store]
        top_store_pct = (top_store_spend / max(grand, 1.0)) * 100
        top_store_visits = store_counts.get(top_store, 1)
        insights.append(
            f"Merchant Concentration: {top_store} captured {top_store_pct:.1f}% of monthly outlay ({curr}{top_store_spend:,.2f} across {top_store_visits} visits)."
        )

    # 7. Store Pricing Arbitrage
    if len(store_totals) >= 2:
        store_avg_costs: Dict[str, List[float]] = {}
        for r in receipts:
            s = r.get("store", "Store")
            items_cnt = max(len(r.get("items", [])), 1)
            t = float(r.get("total", 0.0))
            if s not in store_avg_costs:
                store_avg_costs[s] = []
            store_avg_costs[s].append(t / items_cnt)

        stores_sorted = sorted(store_avg_costs.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)
        high_s, low_s = stores_sorted[0], stores_sorted[-1]
        high_avg, low_avg = sum(high_s[1])/len(high_s[1]), sum(low_s[1])/len(low_s[1])
        if high_avg > low_avg * 1.10:
            diff_pct = ((high_avg - low_avg) / low_avg) * 100
            insights.append(
                f"Store Pricing Arbitrage: Average item cost at {high_s[0]} was {curr}{high_avg:,.2f} vs {curr}{low_avg:,.2f} at {low_s[0]} ({diff_pct:.0f}% specialty store premium)."
            )

    # 8. Month-over-Month Velocity
    if data.get("prev_month"):
        change = data["mom_change_pct"]
        direction = "increased" if change > 0 else "decreased"
        insights.append(
            f"Month-over-Month Velocity: Total spending has {direction} by {abs(change):.1f}% compared to {data['prev_month']}."
        )
    else:
        insights.append("Baseline Tracking Period: Currently establishing initial expenditure benchmarks for MoM tracking.")


    # 10. Actionable Budget Savings Advice
    if cat_totals.get("Food", 0) > (grand * 0.40):
        insights.append(
            "Actionable Savings Lever: Pantry staples (grains, flours, oils) represent over 40% of grocery outlay. Purchasing warehouse multipacks (5kg+ formats) typically yields 12–18% recurring margin savings."
        )
    if "Dining" in cat_totals and "Food" in cat_totals:
        ratio = cat_totals["Dining"] / max(cat_totals["Food"], 1.0)
        if ratio > 0.35:
            insights.append(
                f"Budget Opportunity: Dining out represents {ratio*100:.0f}% of home grocery spend. Meal prepping twice a week could reduce dining variance by up to {curr}{cat_totals['Dining']*0.30:,.2f}."
            )

    return insights


def query_financial_advisor(query: str, month_key: Optional[str] = None) -> str:
    """Natural language financial intelligence engine for spending, budget, and transaction analysis."""
    if not query or not query.strip():
        return "Please enter a specific question regarding spending, categories, merchants, or budget pacing."

    is_yearly = month_key and len(str(month_key).strip()) == 4 and str(month_key).strip().isdigit()
    if is_yearly:
        from storage import get_yearly_data
        data = get_yearly_data(month_key)
    else:
        data = get_monthly_data(month_key)

    if not data or not data.get("receipts"):
        return "No transactions are recorded for this statement period. Digitize receipts in Scan & Verify to populate analysis."

    period_lbl = data.get("month") or data.get("year", "Selected Period")
    q = query.lower().strip()
    curr = data.get("currency", "₹")
    grand = float(data.get("grand_total", 0.0))
    receipts = data.get("receipts", [])
    cat_totals = data.get("category_totals", {})
    store_totals = data.get("store_totals", {})
    store_counts = data.get("store_counts", {})

    all_items = []
    for r in receipts:
        for it in r.get("items", []):
            all_items.append({
                "name": it.get("item_name", "Item"),
                "amount": float(it.get("amount", 0.0)),
                "category": it.get("category", "Other"),
                "store": r.get("store", "Store"),
                "date": r.get("date", ""),
                "qty": float(it.get("quantity", 1.0)),
                "unit_price": float(it.get("unit_price", 0.0))
            })

    all_items.sort(key=lambda x: x["amount"], reverse=True)

    # 1. Specific Item Search (e.g., "did I buy milk?", "how much on coffee?", "paracetamol price")
    search_keywords = ["did i buy", "did i purchase", "how much did i spend on", "how much for", "price of", "cost of", "find item", "search item"]
    target_item_query = ""
    for prefix in search_keywords:
        if prefix in q:
            target_item_query = q.split(prefix, 1)[1].strip(" ?.").strip()
            break

    # If no explicit prefix, check if query matches any purchased item name
    if not target_item_query and len(q.split()) <= 4:
        for it in all_items:
            it_name_lower = it["name"].lower()
            tokens = [t for t in q.replace("?", "").split() if t not in ["the", "a", "an", "any", "my", "our", "did", "i", "we", "much", "spent", "on"]]
            if tokens and any(tok in it_name_lower for tok in tokens if len(tok) >= 3):
                target_item_query = " ".join(tokens)
                break

    if target_item_query and len(target_item_query) >= 3:
        matching_items = [
            it for it in all_items
            if target_item_query in it["name"].lower() or any(w in it["name"].lower() for w in target_item_query.split() if len(w) >= 3)
        ]
        if matching_items:
            match_total = sum(it["amount"] for it in matching_items)
            lines = [
                f"### Itemized Purchase Details: '{target_item_query.title()}' ({period_lbl})",
                f"Found **{len(matching_items)} matching purchase(s)** totaling **{curr}{match_total:,.2f}**:\n",
                "| Date | Merchant | Item Description | Category | Qty | Unit Price | Total Amount |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
            ]
            for it in matching_items:
                u_price = it["unit_price"] if it["unit_price"] > 0 else (it["amount"] / max(it["qty"], 1.0))
                lines.append(
                    f"| {it['date']} | {it['store']} | {it['name']} | {it['category']} | {it['qty']:.0f} | {curr}{u_price:,.2f} | {curr}{it['amount']:,.2f} |"
                )
            lines.append(f"\nAverage unit acquisition cost: **{curr}{(match_total / max(sum(it['qty'] for it in matching_items), 1)):,.2f}**.")
            return "\n".join(lines)

    # 2. 50/30/20 Budget Framework Analysis
    if any(w in q for w in ["50/30/20", "framework", "needs", "wants", "budget analysis", "budget rule"]):
        needs_cats = {"Food", "Household", "Medical", "Utilities", "Transportation"}
        wants_cats = {"Dining", "Drinks", "Bakery", "Entertainment", "Apparel", "Electronics"}

        needs_spend = sum(cat_totals.get(c, 0.0) for c in needs_cats)
        wants_spend = sum(cat_totals.get(c, 0.0) for c in wants_cats)
        other_spend = grand - (needs_spend + wants_spend)

        needs_pct = (needs_spend / max(grand, 1.0)) * 100
        wants_pct = (wants_spend / max(grand, 1.0)) * 100
        other_pct = (other_spend / max(grand, 1.0)) * 100

        lines = [
            f"### 50/30/20 Budget Framework Evaluation ({period_lbl})",
            f"Total Audited Expenditure: **{curr}{grand:,.2f}**\n",
            "| Expense Tier | Category Scope | Actual Spend | Actual Share | Benchmark Target | Variance |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
            f"| **Needs (Essential)** | Groceries, Utilities, Health, Commute | {curr}{needs_spend:,.2f} | {needs_pct:.1f}% | 50.0% | {'+' if needs_pct > 50 else ''}{needs_pct - 50:.1f}% |",
            f"| **Wants (Discretionary)** | Dining, Entertainment, Apparel, Tech | {curr}{wants_spend:,.2f} | {wants_pct:.1f}% | 30.0% | {'+' if wants_pct > 30 else ''}{wants_pct - 30:.1f}% |",
            f"| **Other / Unclassified** | Home & Hardware, Stationery, Misc | {curr}{other_spend:,.2f} | {other_pct:.1f}% | 20.0% (Savings Buffer) | — |",
            "\n**Financial Assessment:**",
        ]
        if wants_pct > 35:
            lines.append(f"- **High Discretionary Allocation**: Discretionary expenditure represents {wants_pct:.1f}% of total outflow (target <= 30.0%). Moderating dining and entertainment could reclaim up to {curr}{wants_spend - (grand * 0.30):,.2f}.")
        else:
            lines.append(f"- **Sound Capital Discipline**: Discretionary purchases are constrained within {wants_pct:.1f}% of total spend, aligning with prudent financial targets.")

        if needs_pct > 65:
            lines.append(f"- **Heavy Essentials Weight**: Essential living requirements account for {needs_pct:.1f}% of total outflow. Review utility consumption and grocery format sizing to optimize baseline operational costs.")
        else:
            lines.append(f"- **Balanced Baseline**: Essentials represent {needs_pct:.1f}% of total allocation, leaving sufficient room for discretionary flexibility and emergency reserves.")

        return "\n".join(lines)

    # 3. Peak / Largest Single-Item Outlays
    if any(w in q for w in ["biggest", "most expensive", "highest", "costliest", "peak", "largest", "top purchase", "top expense"]):
        top_items = all_items[:5]
        lines = [
            f"### Top Line-Item Expenditures ({period_lbl})",
            f"Peak single-item outflow was **{top_items[0]['name']}** for **{curr}{top_items[0]['amount']:,.2f}** at *{top_items[0]['store']}* ({top_items[0]['date']}).\n",
            "| Rank | Item Description | Category | Merchant | Amount | Share of Total |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ]
        for idx, item in enumerate(top_items, 1):
            pct = (item["amount"] / max(grand, 1.0)) * 100
            lines.append(f"| {idx} | **{item['name']}** | {item['category']} | {item['store']} | {curr}{item['amount']:,.2f} | {pct:.1f}% |")
        lines.append(f"\nTop {len(top_items)} purchases collectively represent **{curr}{sum(it['amount'] for it in top_items):,.2f}** ({(sum(it['amount'] for it in top_items)/max(grand, 1.0))*100:.1f}% of monthly outlay).")
        return "\n".join(lines)

    # 4. Detailed Category Breakdown & Specific Category Inquiries
    if any(w in q for w in ["break down", "breakdown", "by category", "categories", "where did my money go", "all categories"]):
        lines = [
            f"### Category Expenditure Allocation ({period_lbl})",
            f"Total Spend: **{curr}{grand:,.2f}** across **{len(receipts)} receipts** and **{len(all_items)} line items**.\n",
            "| Category | Total Outlay | Allocation Share | Item Count | Avg Cost / Item |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for cat, amt in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / max(grand, 1.0)) * 100
            cnt = data.get("category_counts", {}).get(cat, 0)
            avg_it = amt / max(cnt, 1)
            lines.append(f"| **{cat}** | {curr}{amt:,.2f} | {pct:.1f}% | {cnt} | {curr}{avg_it:,.2f} |")
        return "\n".join(lines)

    category_aliases = {
        "Food": ["food", "grocery", "groceries", "supermarket", "staples", "vegetables", "fruits"],
        "Drinks": ["drinks", "beverage", "beverages", "tea", "coffee", "juice", "soda"],
        "Dining": ["dining", "restaurant", "restaurants", "cafe", "takeout", "eating out"],
        "Bakery": ["bakery", "bread", "pastry", "cookies", "cakes", "brownie"],
        "Household": ["household", "cleaning", "detergent", "laundry", "toiletries"],
        "Personal Care": ["personal care", "hygiene", "soap", "shampoo", "skincare"],
        "Medical": ["medical", "medicine", "medicines", "pharmacy", "healthcare", "pharma"],
        "Transportation": ["transport", "transportation", "fuel", "petrol", "cab", "uber", "commute", "transit"],
        "Electronics": ["electronics", "tech", "gadget", "charger", "hardware tech"],
        "Apparel": ["apparel", "clothing", "clothes", "garments", "fashion"],
        "Home & Hardware": ["home & hardware", "hardware", "tools", "repair", "electrical"],
        "Entertainment": ["entertainment", "netflix", "movies", "streaming", "subscriptions"],
        "Utilities": ["utilities", "electricity", "power", "internet", "wifi", "broadband", "water bill"],
        "Office & Stationery": ["stationery", "office", "paper", "pen", "books"]
    }

    matched_cat = None
    for canonical, aliases in category_aliases.items():
        if any(re.search(r"\b" + re.escape(alias) + r"\b", q) for alias in aliases):
            matched_cat = canonical
            break

    if matched_cat:
        cat_amt = cat_totals.get(matched_cat, 0.0)
        cat_pct = (cat_amt / max(grand, 1.0)) * 100
        cat_items = [it for it in all_items if it["category"].lower() == matched_cat.lower()]
        lines = [
            f"### Category Audit: {matched_cat} ({period_lbl})",
            f"Total Outlay: **{curr}{cat_amt:,.2f}** ({cat_pct:.1f}% of total budget across {len(cat_items)} item purchases).\n",
            "| Date | Merchant | Item Description | Qty | Unit Price | Amount |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ]
        for it in cat_items:
            u_p = it["unit_price"] if it["unit_price"] > 0 else (it["amount"] / max(it["qty"], 1.0))
            lines.append(f"| {it['date']} | {it['store']} | {it['name']} | {it['qty']:.0f} | {curr}{u_p:,.2f} | {curr}{it['amount']:,.2f} |")
        return "\n".join(lines)

    # 5. Merchant & Store Breakdown
    if any(w in q for w in ["store", "merchant", "vendor", "retailer", "where do i shop", "shops", "supermarket"]):
        lines = [
            f"### Merchant Concentration Analysis ({period_lbl})",
            f"Expenditure across **{len(store_totals)} distinct merchants** totaling **{curr}{grand:,.2f}**:\n",
            "| Merchant / Vendor | Total Outlay | Share of Wallet | Visit Count | Avg Spend / Visit |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for store, amt in sorted(store_totals.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / max(grand, 1.0)) * 100
            visits = store_counts.get(store, 1)
            avg_trip = amt / visits
            lines.append(f"| **{store}** | {curr}{amt:,.2f} | {pct:.1f}% | {visits} | {curr}{avg_trip:,.2f} |")
        return "\n".join(lines)

    # 6. Actionable Cost Savings Opportunities
    if any(w in q for w in ["save", "saving", "savings", "cut", "reduce", "plan", "recommendation", "advice", "tips"]):
        lines = [
            f"### Strategic Expenditure Optimization ({period_lbl})",
            f"Based on empirical item-level transactions totaling **{curr}{grand:,.2f}**, here are targeted savings levers:\n"
        ]
        recs = []
        if cat_totals.get("Food", 0) > (grand * 0.30):
            food_amt = cat_totals["Food"]
            recs.append(
                f"1. **Bulk Staples Procurement**: Grocery staples represent {curr}{food_amt:,.2f} ({(food_amt/grand)*100:.0f}% of spend). Consolidating pantry dry goods into warehouse sizes (5kg+ formats) typically yields **12% to 18% margin savings** (~{curr}{food_amt * 0.14:,.2f}/month)."
            )
        if "Dining" in cat_totals and cat_totals["Dining"] > 0:
            dining_amt = cat_totals["Dining"]
            recs.append(
                f"2. **Dining vs Home Meal Balance**: Dining out represents {curr}{dining_amt:,.2f}. Substituting two dining occasions per month with home preparation recovers up to **{curr}{dining_amt * 0.35:,.2f}**."
            )
        if len(store_totals) >= 3:
            recs.append(
                f"3. **Trip Batching & Vendor Consolidation**: Shopping across {len(store_totals)} separate venues averages {curr}{data.get('avg_per_receipt', 0.0):,.2f} per transaction. Consolidating into bi-weekly planned trips curtails incidental impulse additions by an estimated **8% to 10%** (~{curr}{grand * 0.09:,.2f})."
            )
        if "Utilities" in cat_totals or "Entertainment" in cat_totals:
            rec_subs = cat_totals.get("Entertainment", 0) + cat_totals.get("Utilities", 0)
            recs.append(
                f"4. **Recurring Subscriptions Review**: Fixed monthly service charges total {curr}{rec_subs:,.2f}. Audit recurring memberships and broadband tiers annually to avoid subscription creep."
            )
        lines.extend(recs if recs else [
            f"1. **Baseline Maintenance**: Discretionary outlays are within standard thresholds. Maintain current tracking cadence to establish quarterly baseline averages."
        ])
        return "\n\n".join(lines)

    # 7. Spending Velocity & Month-End Projections
    if any(w in q for w in ["velocity", "project", "projection", "forecast", "run rate", "end of month", "pace", "burn rate"]):
        latest_date_str = max((r.get("date", "2026-10-18") for r in receipts), default="2026-10-18")
        day = int(latest_date_str.split("-")[2][:2]) if "-" in latest_date_str else 18
        daily_pace = grand / max(day, 1)
        projected = daily_pace * 30

        lines = [
            f"### Spending Velocity and Projections ({period_lbl})",
            f"- **Cumulative Spend to Date:** {curr}{grand:,.2f} (through Day {day} of the period)",
            f"- **Daily Burn Rate:** {curr}{daily_pace:,.2f} / day",
            f"- **Projected Month-End Total:** **{curr}{projected:,.2f}** (30-day constant-velocity projection)",
        ]
        if data.get("prev_month"):
            prev_total = data.get("prev_month_total", 0.0)
            if prev_total > 0:
                var_pct = ((projected - prev_total) / prev_total) * 100
                lines.append(f"- **Baseline Comparison:** Projected run-rate is {abs(var_pct):.1f}% {'higher' if var_pct > 0 else 'lower'} than previous period ({curr}{prev_total:,.2f}).")
        return "\n\n".join(lines)

    # 8. Payment Method Distribution
    if any(w in q for w in ["payment", "card", "upi", "cash", "net banking", "how did i pay"]):
        pay_totals = defaultdict(float)
        pay_counts = defaultdict(int)
        for r in receipts:
            m = r.get("payment_method") or "Unspecified"
            pay_totals[m] += float(r.get("total", 0.0))
            pay_counts[m] += 1

        lines = [
            f"### Payment Method Distribution ({period_lbl})",
            "| Payment Method | Total Outflow | Share | Transaction Count | Avg / Transaction |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for m, amt in sorted(pay_totals.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / max(grand, 1.0)) * 100
            cnt = pay_counts[m]
            lines.append(f"| **{m}** | {curr}{amt:,.2f} | {pct:.1f}% | {cnt} | {curr}{amt/cnt:,.2f} |")
        return "\n".join(lines)

    # 9. Tax & Compliance Breakdown
    if any(w in q for w in ["tax", "gst", "vat", "compliance"]):
        total_tax = sum(float(r.get("tax", 0.0)) for r in receipts)
        total_subtotal = sum(float(r.get("subtotal", 0.0)) for r in receipts)
        eff_tax_rate = (total_tax / max(total_subtotal, 1.0)) * 100

        lines = [
            f"### Indirect Tax Breakdown ({period_lbl})",
            f"- **Cumulative Tax (GST/VAT) Paid:** {curr}{total_tax:,.2f}",
            f"- **Pre-Tax Subtotal:** {curr}{total_subtotal:,.2f}",
            f"- **Gross Total:** {curr}{grand:,.2f}",
            f"- **Effective Average Tax Rate:** {eff_tax_rate:.2f}% across {len(receipts)} audited transactions.",
        ]
        return "\n\n".join(lines)

    # 10. Month-over-Month Comparisons & Historical Trends
    if any(w in q for w in ["compare", "trend", "history", "previous", "august", "september", "october", "months"]):
        from storage import get_available_months
        avail = get_available_months()
        lines = [
            "### Historical Spending Velocity Overview",
            "| Statement Month | Gross Expenditure | Audited Receipts | Primary Driver | MoM Change |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        prev_amt = None
        for m in sorted(avail):
            m_data = get_monthly_data(m)
            m_total = m_data.get("grand_total", 0.0)
            m_cnt = m_data.get("receipt_count", 0)
            m_top = m_data.get("top_category", "None")
            mom_str = "Baseline"
            if prev_amt is not None and prev_amt > 0:
                diff = ((m_total - prev_amt) / prev_amt) * 100
                mom_str = f"{'+' if diff > 0 else ''}{diff:.1f}%"
            prev_amt = m_total
            lines.append(f"| **{m}** | {curr}{m_total:,.2f} | {m_cnt} | {m_top} | {mom_str} |")
        return "\n".join(lines)

    # 11. General Executive Financial Brief (Fallback)
    top_store = max(store_totals, key=store_totals.get) if store_totals else "—"
    top_store_val = max(store_totals.values()) if store_totals else 0.0
    return (
        f"### Financial Summary ({period_lbl})\n\n"
        f"| Metric | Value |\n"
        f"| :--- | :--- |\n"
        f"| Total Outflow | {curr}{grand:,.2f} |\n"
        f"| Audited Receipts | {len(receipts)} transactions |\n"
        f"| Line Items Acquired | {len(all_items)} products |\n"
        f"| Average Ticket Size | {curr}{data.get('avg_per_receipt', 0.0):,.2f} / trip |\n"
        f"| Primary Expense Category | {data.get('top_category', 'None')} ({curr}{cat_totals.get(data.get('top_category', ''), 0.0):,.2f}) |\n"
        f"| Primary Merchant | {top_store} ({curr}{top_store_val:,.2f}) |\n\n"
        f"Suggested Inquiries:\n"
        f"- What was my largest purchase?\n"
        f"- Break down spending by category\n"
        f"- Analyze spending across merchants\n"
        f"- Provide a 50/30/20 budget analysis\n"
        f"- What are the highest impact savings opportunities?"
    )


# ─── Data Export ─────────────────────────────────────────────────────────────
def export_to_csv(month_key: Optional[str] = None) -> Optional[str]:
    """Export itemized transactions to CSV for selected month or year."""
    if month_key and len(str(month_key).strip()) == 4 and str(month_key).strip().isdigit():
        from storage import get_yearly_data
        data = get_yearly_data(month_key)
        period_lbl = str(month_key)
    else:
        data = get_monthly_data(month_key)
        period_lbl = data.get("month", "data") if data else "data"

    if not data or not data.get("receipts"):
        return None

    curr = data.get("currency", "₹")
    rows = []
    for r in data["receipts"]:
        for item in r.get("items", []):
            rows.append({
                "Receipt ID": r.get("id", ""),
                "Date": r.get("date", ""),
                "Merchant": r.get("store", ""),
                "Currency": r.get("currency", curr),
                "Item Name": item.get("item_name", ""),
                "Category": item.get("category", "Other"),
                "Amount": item.get("amount", 0.0),
                "Quantity": item.get("quantity", 1.0),
                "Unit Price": item.get("unit_price", 0.0),
                "Confidence": item.get("confidence", 0.0),
                "Receipt Total": r.get("total", 0.0)
            })

    os.makedirs(CHARTS_DIR, exist_ok=True)
    csv_path = os.path.join(CHARTS_DIR, f"expenses_{period_lbl}.csv")
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    return csv_path


def export_to_json(month_key: Optional[str] = None) -> Optional[str]:
    """Export full structured monthly or yearly dataset to JSON."""
    if month_key and len(str(month_key).strip()) == 4 and str(month_key).strip().isdigit():
        from storage import get_yearly_data
        data = get_yearly_data(month_key)
        period_lbl = str(month_key)
    else:
        data = get_monthly_data(month_key)
        period_lbl = data.get("month", "data") if data else "data"

    if not data:
        return None

    os.makedirs(CHARTS_DIR, exist_ok=True)
    json_path = os.path.join(CHARTS_DIR, f"expenses_{period_lbl}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return json_path