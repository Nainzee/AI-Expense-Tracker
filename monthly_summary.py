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
    "Household": "#F59E0B",     # Amber
    "Personal Care": "#8B5CF6", # Purple
    "Medical": "#EF4444",       # Rose / Red
    "Bakery": "#EC4899",        # Pink
    "Dining": "#F97316",        # Orange
    "Electronics": "#06B6D4",   # Cyan
    "Apparel": "#6366F1",       # Indigo
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
    get_monthly_data,
    record_user_correction,
    get_user_correction,
)


# ─── Chart Generation (Publication Quality) ──────────────────────────────────
def generate_monthly_chart(month_key: Optional[str] = None) -> Optional[str]:
    """
    Generate modern high-resolution spending charts:
    Left: Category Breakdown Donut chart with center total callout.
    Right: Horizontal Ranked Bar chart with precise amounts.
    """
    data = get_monthly_data(month_key)
    if not data or not data["category_totals"]:
        return None

    curr = data.get("currency", "₹")
    os.makedirs(CHARTS_DIR, exist_ok=True)
    chart_path = os.path.join(CHARTS_DIR, f"chart_{data['month']}.png")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), dpi=150)
    fig.patch.set_facecolor("#0F172A")

    # ── Panel 1: Donut Chart ─────────────────────────────────────────────────
    ax1 = axes[0]
    ax1.set_facecolor("#0F172A")

    cats = list(data["category_totals"].keys())
    # Exclude negative discounts from donut proportions
    amts = [max(0.0, data["category_totals"][c]) for c in cats]
    colors = [get_category_color(c) for c in cats]

    wedges, texts = ax1.pie(
        amts,
        colors=colors,
        startangle=140,
        wedgeprops={"width": 0.48, "edgecolor": "#0F172A", "linewidth": 3}
    )

    # Center callout
    ax1.text(0, 0.08, f"{curr}{data['grand_total']:,.2f}", ha="center", va="center",
             fontsize=17, fontweight="bold", color="#F8FAFC")
    ax1.text(0, -0.15, "TOTAL SPEND", ha="center", va="center",
             fontsize=9, fontweight="medium", color="#94A3B8")

    legend_labels = [f"{c} ({curr}{v:,.0f})" for c, v in zip(cats, amts)]
    ax1.legend(wedges, legend_labels, loc="lower center", bbox_to_anchor=(0.5, -0.15),
               ncol=2, fontsize=8.5, frameon=False, labelcolor="#E2E8F0")
    ax1.set_title("Spending by Category", fontsize=13, fontweight="bold", pad=15, color="#F8FAFC")

    # ── Panel 2: Ranked Bar Chart ────────────────────────────────────────────
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
        ax2.text(
            bar.get_width() + max_amt * 0.03,
            bar.get_y() + bar.get_height() / 2,
            f"{curr}{amt:,.2f} ({pct:.0f}%)",
            va="center", ha="left", fontsize=9.5, color="#E2E8F0"
        )

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


DEFAULT_CATEGORY_BUDGETS: Dict[str, float] = {
    "Food": 15000.0,
    "Drinks": 3000.0,
    "Household": 4000.0,
    "Personal Care": 3500.0,
    "Medical": 3000.0,
    "Bakery": 2000.0,
    "Dining": 5000.0,
    "Electronics": 10000.0,
    "Apparel": 5000.0,
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

    # 0.5. Month-End Velocity Forecast
    forecast = calculate_month_end_forecast(data)
    insights.append(
        f"Month-End Velocity Forecast: Paced at {curr}{forecast['daily_velocity']:,.2f}/day through Day {forecast['day_progress']}. "
        f"Projected month-end outlay is ~{curr}{forecast['projected_total']:,.2f} ({forecast['days_in_month']}-day projection)."
    )


    # 1. Executive Health Score & Discipline Index
    essential_cats = {"Food", "Medical", "Household", "Personal Care"}
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
    """Interactive natural language Q&A engine for personalized spending intelligence."""
    if not query or not query.strip():
        return "Please enter a question about your spending or budget."

    data = get_monthly_data(month_key)
    if not data or not data.get("receipts"):
        return "No transactions are recorded for this month yet. Scan receipts in Tab 1 to activate the AI Financial Advisor."

    q = query.lower().strip()
    curr = data.get("currency", "₹")
    grand = float(data.get("grand_total", 0.0))
    receipts = data.get("receipts", [])
    cat_totals = data.get("category_totals", {})
    store_totals = data.get("store_totals", {})

    all_items = []
    for r in receipts:
        for it in r.get("items", []):
            all_items.append({
                "name": it.get("item_name", "Item"),
                "amount": float(it.get("amount", 0.0)),
                "category": it.get("category", "Other"),
                "store": r.get("store", "Store"),
                "date": r.get("date", ""),
                "qty": float(it.get("quantity", 1.0))
            })

    all_items.sort(key=lambda x: x["amount"], reverse=True)

    # 1. Biggest / Most expensive purchase
    if any(w in q for w in ["biggest", "most expensive", "highest", "costliest", "peak", "top purchase"]):
        top_3 = all_items[:3]
        lines = [
            f"### 🏷️ Top Single-Item Purchases in {data['month']}",
            f"Your peak expense was **{top_3[0]['name']}** for **{curr}{top_3[0]['amount']:,.2f}** at *{top_3[0]['store']}* ({top_3[0]['date']}).\n",
            "**Top items ranked by expenditure:**"
        ]
        for idx, item in enumerate(top_3, 1):
            pct = (item["amount"] / max(grand, 1.0)) * 100
            lines.append(f"{idx}. **{item['name']}** — {curr}{item['amount']:,.2f} ({pct:.1f}% of total spend) | *{item['store']}*")
        return "\n\n".join(lines)

    # 2. Detailed category breakdown
    if any(w in q for w in ["break down", "breakdown", "category", "categories", "where did my money go"]):
        lines = [
            f"### 📊 Detailed Category Breakdown for {data['month']}",
            f"Total Spend: **{curr}{grand:,.2f}** across **{len(receipts)} trips** and **{len(all_items)} items**.\n",
            "| Category | Total Spent | Share | Item Count |",
            "| :--- | :--- | :--- | :--- |"
        ]
        for cat, amt in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / max(grand, 1.0)) * 100
            cnt = data["category_counts"].get(cat, 0)
            lines.append(f"| **{cat}** | {curr}{amt:,.2f} | {pct:.1f}% | {cnt} |")
        return "\n".join(lines)

    # 3. Specific category lookup (Food, Drinks, Medical, etc.)
    for cat in ["Food", "Drinks", "Household", "Personal Care", "Medical", "Bakery", "Dining", "Electronics", "Apparel"]:
        if cat.lower() in q:
            cat_amt = cat_totals.get(cat, 0.0)
            cat_pct = (cat_amt / max(grand, 1.0)) * 100
            cat_items = [it for it in all_items if it["category"].lower() == cat.lower()]
            lines = [
                f"### 🛒 Spending on {cat} in {data['month']}",
                f"You spent **{curr}{cat_amt:,.2f}** on **{cat}**, which accounts for **{cat_pct:.1f}%** of your total monthly expenditure across **{len(cat_items)} items**.\n",
                "**Items in this category:**"
            ]
            for it in cat_items:
                lines.append(f"• **{it['name']}**: {curr}{it['amount']:,.2f} (Qty: {it['qty']}) at *{it['store']}*")
            return "\n\n".join(lines)

    # 4. Store / Merchant breakdown
    if any(w in q for w in ["store", "merchant", "vendor", "where do i shop", "supermarket", "shops"]):
        lines = [
            f"### 🏪 Merchant Breakdown for {data['month']}",
            f"You shopped at **{len(store_totals)} unique stores** this month:\n"
        ]
        for store, amt in sorted(store_totals.items(), key=lambda x: x[1], reverse=True):
            pct = (amt / max(grand, 1.0)) * 100
            visits = data["store_counts"].get(store, 1)
            lines.append(f"• **{store}**: **{curr}{amt:,.2f}** ({pct:.1f}% of total) across **{visits} visit{'s' if visits > 1 else ''}** (Avg: {curr}{amt/visits:,.2f}/trip)")
        return "\n\n".join(lines)

    # 5. Budget / Savings Advice
    if any(w in q for w in ["save", "saving", "cut", "reduce", "budget", "plan", "advice", "tips"]):
        lines = [
            f"### 🎯 Personalized Budget & Savings Plan ({data['month']})",
            f"Based on your actual purchasing patterns totaling **{curr}{grand:,.2f}**, here are 3 high-impact strategies:\n",
            f"1. **Bulk Pantry Arbitrage**: Your highest individual purchase was *{all_items[0]['name']}* ({curr}{all_items[0]['amount']:,.2f}). Consolidating grains, oils, and non-perishables into 5kg/5L packs typically captures a **12–18% recurring price discount**.",
            f"2. **Store Optimization**: You shopped across {len(store_totals)} stores. Prioritizing primary supermarkets for bulk staples and reserving specialty stores exclusively for items unavailable elsewhere could save up to **{curr}{grand*0.12:,.2f}/month**.",
            f"3. **Zero Waste Batching**: You averaged **{curr}{data['avg_per_receipt']:,.2f}** per shopping trip. Combining multiple weekly trips into planned bi-weekly runs reduces impulse add-on purchases by an estimated **8–10%**."
        ]
        return "\n\n".join(lines)

    # 6. Projection / Run-rate
    if any(w in q for w in ["project", "forecast", "run rate", "end of month", "pace", "spend by end"]):
        latest_date_str = max(r.get("date", "2026-10-18") for r in receipts)
        day = int(latest_date_str.split("-")[2][:2]) if "-" in latest_date_str else 18
        projected = (grand / max(day, 1)) * 30
        return (
            f"### 📈 Month-End Projection for {data['month']}\n\n"
            f"• **Current Total:** {curr}{grand:,.2f} through Day {day}\n"
            f"• **Daily Velocity:** {curr}{grand/day:,.2f} / day\n"
            f"• **Projected Month-End Spend:** **~{curr}{projected:,.2f}** (assuming constant velocity)\n\n"
            f"*Tip: Keep track of mid-month discretionary spending to maintain your target monthly budget.*"
        )

    # Fallback: Comprehensive AI Financial Advisory Brief
    return (
        f"### 💡 AI Financial Advisor Brief ({data['month']})\n\n"
        f"• **Total Recorded Spend:** {curr}{grand:,.2f} across {len(receipts)} receipts\n"
        f"• **Dominant Category:** {data.get('top_category', 'Food')} ({curr}{cat_totals.get(data.get('top_category', 'Food'), 0.0):,.2f})\n"
        f"• **Top Merchant:** {max(store_totals, key=store_totals.get) if store_totals else '—'} ({curr}{max(store_totals.values()) if store_totals else 0:,.2f})\n"
        f"• **Average Basket Size:** {curr}{data.get('avg_per_receipt', 0.0):,.2f} per shopping trip\n\n"
        f"Try asking:\n"
        f"- *'What was my biggest expense?'*\n"
        f"- *'Break down my spending by category'* \n"
        f"- *'How can I save money this month?'*\n"
        f"- *'How much did I spend on Food?'*"
    )


# ─── Data Export ─────────────────────────────────────────────────────────────
def export_to_csv(month_key: Optional[str] = None) -> Optional[str]:
    """Export itemized transactions to CSV."""
    data = get_monthly_data(month_key)
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
    csv_path = os.path.join(CHARTS_DIR, f"expenses_{data['month']}.csv")
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)
    return csv_path


def export_to_json(month_key: Optional[str] = None) -> Optional[str]:
    """Export full structured monthly dataset to JSON."""
    data = get_monthly_data(month_key)
    if not data:
        return None

    os.makedirs(CHARTS_DIR, exist_ok=True)
    json_path = os.path.join(CHARTS_DIR, f"expenses_{data['month']}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return json_path