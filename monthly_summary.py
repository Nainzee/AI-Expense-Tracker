
import json
import os
from datetime import datetime
from collections import defaultdict
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # Use non-interactive backend (important for Gradio)
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

DB_PATH = "receipts_db/receipts.json"

# ─── Category color palette ──────────────────────────────────────────────────
CATEGORY_COLORS = {
    "Food":          "#1D9E75",
    "Drinks":        "#378ADD",
    "Household":     "#BA7517",
    "Personal Care": "#9B59B6",
    "Medical":       "#E24B4A",
    "Bakery":        "#E67E22",
    "Other":         "#888780"
}

def get_color(cat: str) -> str:
    return CATEGORY_COLORS.get(cat, "#888780")


# ─── Database helpers ────────────────────────────────────────────────────────
def load_db() -> dict:
    os.makedirs("receipts_db", exist_ok=True)
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH) as f:
        return json.load(f)


def save_db(db: dict):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)


def save_receipt(store_name: str, items: list[dict], scan_date: str = None) -> str:
    """
    Save a scanned receipt to the database.
    Returns the receipt ID.
    """
    db   = load_db()
    date = scan_date or datetime.now().strftime("%Y-%m-%d")
    key  = datetime.now().strftime("%Y-%m")   # Month key e.g. "2025-03"

    if key not in db:
        db[key] = []

    receipt_id = f"R{len(db[key])+1:03d}"
    total      = sum(item["amount"] for item in items)

    db[key].append({
        "id":         receipt_id,
        "store":      store_name or "Unknown Store",
        "date":       date,
        "items":      items,
        "total":      round(total, 2)
    })

    save_db(db)
    return receipt_id


def get_monthly_data(month_key: str = None) -> dict:
    """
    Aggregate all receipts for a given month.
    month_key format: "YYYY-MM" e.g. "2025-03"
    If None, uses current month.
    """
    db    = load_db()
    month = month_key or datetime.now().strftime("%Y-%m")

    if month not in db:
        return None

    receipts = db[month]

    # Aggregate by category
    category_totals = defaultdict(float)
    category_counts = defaultdict(int)
    all_items       = []

    for receipt in receipts:
        for item in receipt["items"]:
            cat = item.get("category", "Other")
            category_totals[cat] += item["amount"]
            category_counts[cat] += 1
            all_items.append(item)

    grand_total = sum(r["total"] for r in receipts)

    # Weekly breakdown
    weekly = defaultdict(float)
    for receipt in receipts:
        try:
            day  = int(receipt["date"].split("-")[2])
            week = f"Week {(day - 1) // 7 + 1}"
        except Exception:
            week = "Week 1"
        weekly[week] += receipt["total"]

    return {
        "month":            month,
        "receipts":         receipts,
        "receipt_count":    len(receipts),
        "grand_total":      round(grand_total, 2),
        "avg_per_receipt":  round(grand_total / len(receipts), 2) if receipts else 0,
        "category_totals":  dict(category_totals),
        "category_counts":  dict(category_counts),
        "weekly":           dict(weekly),
        "top_category":     max(category_totals, key=category_totals.get) if category_totals else "—"
    }


def get_available_months() -> list[str]:
    db = load_db()
    return sorted(db.keys(), reverse=True)


# ─── Chart generation ────────────────────────────────────────────────────────
def generate_monthly_chart(month_key: str = None) -> str | None:
    """
    Generate a spending breakdown chart for the month.
    Returns the path to the saved PNG image, or None if no data.
    """
    data = get_monthly_data(month_key)
    if not data:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.patch.set_facecolor("#F8F8F6")

    # ── Left: Category bar chart
    ax1 = axes[0]
    ax1.set_facecolor("#F8F8F6")

    cats    = list(data["category_totals"].keys())
    amounts = list(data["category_totals"].values())
    colors  = [get_color(c) for c in cats]

    # Sort by amount descending
    sorted_pairs = sorted(zip(amounts, cats, colors), reverse=True)
    amounts, cats, colors = zip(*sorted_pairs) if sorted_pairs else ([], [], [])

    bars = ax1.barh(cats, amounts, color=colors, height=0.6, edgecolor="none")

    for bar, amt in zip(bars, amounts):
        ax1.text(
            bar.get_width() + max(amounts) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"₹{amt:.0f}", va="center", ha="left", fontsize=10, color="#444"
        )

    ax1.set_xlabel("Amount (₹)", fontsize=10, color="#666")
    ax1.set_title("Spending by Category", fontsize=13, fontweight="bold", pad=12, color="#222")
    ax1.spines[["top", "right", "left"]].set_visible(False)
    ax1.tick_params(axis="y", labelsize=10, colors="#444")
    ax1.tick_params(axis="x", labelsize=9,  colors="#888")
    ax1.set_xlim(0, max(amounts) * 1.25 if amounts else 10)

    # ── Right: Donut / pie chart
    ax2 = axes[1]
    ax2.set_facecolor("#F8F8F6")

    if data["category_totals"]:
        pie_cats    = list(data["category_totals"].keys())
        pie_amounts = list(data["category_totals"].values())
        pie_colors  = [get_color(c) for c in pie_cats]

        wedges, _ = ax2.pie(
            pie_amounts,
            colors=pie_colors,
            startangle=140,
            wedgeprops={"width": 0.55, "edgecolor": "#F8F8F6", "linewidth": 2}
        )

        # Centre text
        ax2.text(0, 0.1,  f"₹{data['grand_total']:.0f}", ha="center", va="center",
                 fontsize=15, fontweight="bold", color="#222")
        ax2.text(0, -0.2, "total", ha="center", va="center",
                 fontsize=10, color="#888")

        # Legend
        legend_patches = [
            mpatches.Patch(color=get_color(c), label=f"{c} ({v/data['grand_total']*100:.0f}%)")
            for c, v in zip(pie_cats, pie_amounts)
        ]
        ax2.legend(handles=legend_patches, loc="lower center",
                   bbox_to_anchor=(0.5, -0.18), ncol=2, fontsize=9,
                   frameon=False)

    ax2.set_title("Category Split", fontsize=13, fontweight="bold", pad=12, color="#222")

    plt.tight_layout(pad=2)
    chart_path = "receipts_db/monthly_chart.png"
    plt.savefig(chart_path, dpi=130, bbox_inches="tight", facecolor="#F8F8F6")
    plt.close()

    return chart_path


# ─── AI Insights ─────────────────────────────────────────────────────────────
def generate_insights(data: dict) -> list[str]:
    """
    Generate simple rule-based insights from monthly spending data.
    These are smart observations that make the app feel intelligent.
    """
    if not data:
        return ["No data yet for this month."]

    insights  = []
    cat_total = data["category_totals"]
    grand     = data["grand_total"]

    # Insight 1: Top spending category
    if cat_total:
        top_cat = max(cat_total, key=cat_total.get)
        top_pct = cat_total[top_cat] / grand * 100
        insights.append(
            f"Your biggest expense this month is {top_cat} "
            f"at ₹{cat_total[top_cat]:.0f} ({top_pct:.0f}% of total spend)."
        )

    # Insight 2: Number of shopping trips
    insights.append(
        f"You made {data['receipt_count']} shopping trips this month, "
        f"averaging ₹{data['avg_per_receipt']:.0f} per trip."
    )

    # Insight 3: Medical spending flag
    if "Medical" in cat_total and cat_total["Medical"] > grand * 0.15:
        insights.append(
            f"Medical expenses are higher than usual this month "
            f"(₹{cat_total['Medical']:.0f}). Consider reviewing your health insurance."
        )

    # Insight 4: Household vs Food ratio
    if "Household" in cat_total and "Food" in cat_total:
        ratio = cat_total["Household"] / cat_total["Food"]
        if ratio > 0.5:
            insights.append(
                f"Household spending is {ratio*100:.0f}% of your food budget. "
                f"Buying cleaning supplies in bulk could reduce costs."
            )

    # Insight 5: Most frequent store
    store_counts = defaultdict(int)
    for r in data["receipts"]:
        store_counts[r["store"]] += 1
    if store_counts:
        fav_store = max(store_counts, key=store_counts.get)
        insights.append(
            f"You shop most at {fav_store} "
            f"({store_counts[fav_store]} visit{'s' if store_counts[fav_store]>1 else ''} this month)."
        )

    return insights


def export_to_csv(month_key: str = None) -> str:
    """Export all items for a month to a CSV file. Returns file path."""
    data = get_monthly_data(month_key)
    if not data:
        return None

    rows = []
    for receipt in data["receipts"]:
        for item in receipt["items"]:
            rows.append({
                "Date":       receipt["date"],
                "Store":      receipt["store"],
                "Item":       item["item_name"],
                "Category":   item.get("category", "Other"),
                "Amount":     item["amount"],
                "Confidence": item.get("confidence", "—")
            })

    df       = pd.DataFrame(rows)
    csv_path = f"receipts_db/expenses_{data['month']}.csv"
    df.to_csv(csv_path, index=False)
    return csv_path