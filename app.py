"""
Smart Receipt Scanner & Expense Intelligence Platform.

Interactive Web Application built with Gradio 6.x.
Features:
- Dual OCR Engine: Spatial Line Clustering & Vision Transformer.
- Dynamic multi-currency detection ($, €, £, ₹, ¥).
- Scale line attachment ('0.778 kg NET @ $5.99/kg $4.66' -> previous item).
- Negative discount rows preservation ('LOYALTY -15.00' -> -$15.00).
- Payment tender and date line suppression.
- Human-in-the-Loop Interactive Line-Item Editor.
- Reconciliation gap alert / confirmation barrier at save.
- Auto-loading Dashboard with dynamic HTML stat cards and high-res charts.
- Multi-Receipt History Management (CRUD).
- High-Resolution Charting & CSV/JSON Data Exports.
"""

from __future__ import annotations

import os
import glob
from datetime import datetime
from PIL import Image
import pandas as pd
import gradio as gr

from ocr_pipeline import extract_receipt_items
from categorizer import categorize_items, CANONICAL_CATEGORIES
from monthly_summary import (
    save_receipt,
    get_monthly_data,
    get_available_months,
    generate_monthly_chart,
    generate_insights,
    query_financial_advisor,
    export_to_csv,
    export_to_json,
    list_receipts,
    delete_receipt,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Sample Receipts Catalog ────────────────────────────────────────────────
SAMPLE_RECEIPTS = {
    "Grocery Supermart (₹)": os.path.join(BASE_DIR, "sample_receipts", "sample_grocery.png"),
    "Apollo MedPlus Pharmacy (₹)": os.path.join(BASE_DIR, "sample_receipts", "sample_pharmacy.png"),
    "Artisan Cafe & Bistro (₹)": os.path.join(BASE_DIR, "sample_receipts", "sample_dining.png"),
    "Digital Tech Hub (₹)": os.path.join(BASE_DIR, "sample_receipts", "sample_electronics.png"),
}


def load_sample_receipt(sample_name: str):
    """Load sample image when picked from dropdown."""
    if sample_name in SAMPLE_RECEIPTS and os.path.exists(SAMPLE_RECEIPTS[sample_name]):
        return Image.open(SAMPLE_RECEIPTS[sample_name])
    return None


# ─── Tab 1: Scanning & Human-in-the-Loop Editing ─────────────────────────────
def scan_receipt_action(image, engine_choice: str):
    """
    Perform end-to-end receipt extraction with currency and metadata extraction.
    Returns:
        store_name, date, subtotal, tax, grand_total, curr, recon_badge,
        items_df, annotated_img, raw_text, status_msg
    """
    if image is None:
        return (
            "", "", "", "", "", "₹", "Upload a receipt photo to scan",
            pd.DataFrame(columns=["Item Name", "Category", "Amount", "Confidence", "Qty"]),
            None, "", "⚠️ Please upload or select a sample receipt."
        )

    pil_img = Image.fromarray(image) if not isinstance(image, Image.Image) else image
    use_trocr = (engine_choice == "TrOCR (Deep Vision Transformer)")

    try:
        raw_items, raw_text, meta, annotated_img = extract_receipt_items(pil_img, use_trocr=use_trocr)
    except Exception as exc:
        return (
            "", "", "", "", "", "₹", f"OCR Error: {str(exc)}",
            pd.DataFrame(columns=["Item Name", "Category", "Amount", "Confidence", "Qty"]),
            None, "", f"❌ OCR Pipeline Error: {str(exc)}"
        )

    curr = meta.get("currency", "₹")

    if not raw_items:
        return (
            meta.get("store_name", ""), meta.get("date", ""), "", "", "", curr,
            "No line items detected",
            pd.DataFrame(columns=["Item Name", "Category", "Amount", "Confidence", "Qty"]),
            annotated_img, raw_text, "⚠️ No valid line items detected. Check image focus or rotate."
        )

    # Classify items
    try:
        categorized = categorize_items(raw_items)
    except Exception:
        categorized = raw_items

    # Build editable DataFrame
    rows = []
    for it in categorized:
        rows.append({
            "Item Name": it.get("item_name", ""),
            "Category": it.get("category", "Other"),
            "Amount": float(it.get("amount", 0.0)),
            "Confidence": f"{float(it.get('confidence', 0.90))*100:.0f}%",
            "Qty": float(it.get("quantity", 1.0))
        })

    df = pd.DataFrame(rows)

    store = meta.get("store_name", "")
    date_val = meta.get("date", datetime.now().strftime("%Y-%m-%d"))
    subtotal_val = f"{curr}{meta['subtotal']:.2f}" if meta.get("subtotal") else "—"
    tax_val = f"{curr}{meta['tax']:.2f}" if meta.get("tax") else "—"
    total_val = f"{curr}{meta['grand_total']:.2f}" if meta.get("grand_total") is not None else f"{curr}{sum(it['amount'] for it in categorized):.2f}"
    recon = f"Status: {meta.get('reconciliation_status', 'Estimated')}"

    # Prompt user if store name was unreadable
    if not store:
        status_msg = f"Extracted {len(categorized)} items ({curr}). Merchant name was unclear — please enter Store Name below before saving."
    else:
        status_msg = f"Extracted {len(categorized)} items ({curr}) successfully! Review and edit rows below before saving."

    return store, date_val, subtotal_val, tax_val, total_val, curr, recon, df, annotated_img, raw_text, status_msg


def recompute_items_total(df: pd.DataFrame, curr: str) -> str:
    """Calculate live total from the editable table."""
    c = curr or "₹"
    if df is None or df.empty or "Amount" not in df.columns:
        return f"**Calculated Items Total: {c}0.00**"
    try:
        total = df["Amount"].astype(float).sum()
        return f"**Calculated Items Total: {c}{total:.2f}**"
    except Exception:
        return f"**Calculated Items Total: Invalid format**"


def confirm_and_save_receipt(
    store_name: str,
    date_val: str,
    df: pd.DataFrame,
    printed_total_str: str,
    curr: str,
    payment_method: str,
    notes: str,
    force_save: bool
) -> str:
    """
    Commit verified receipt to database with safety check against large discrepancy.
    """
    c = curr or "₹"
    if df is None or df.empty:
        return "⚠️ Cannot save empty receipt."

    items_to_save = []
    for _, row in df.iterrows():
        try:
            amt = float(row.get("Amount", 0.0))
        except (ValueError, TypeError):
            amt = 0.0

        try:
            qty = float(row.get("Qty", 1.0))
        except (ValueError, TypeError):
            qty = 1.0

        items_to_save.append({
            "item_name": str(row.get("Item Name", "Item")),
            "category": str(row.get("Category", "Other")),
            "amount": amt,
            "quantity": qty,
            "confidence": 1.0
        })

    total_amount = round(sum(it["amount"] for it in items_to_save), 2)

    # Parse printed total if available
    printed_val = None
    if printed_total_str and printed_total_str != "—":
        clean_p = printed_total_str.replace(c, "").replace(",", "").strip()
        try:
            printed_val = float(clean_p)
        except ValueError:
            pass

    # Safety check: if discrepancy is significant and user hasn't checked force_save
    if printed_val is not None and abs(total_amount - printed_val) > 2.0 and not force_save:
        diff = abs(total_amount - printed_val)
        return (
            f"⚠️ **Large Reconciliation Discrepancy!**\n\n"
            f"Calculated items total is **{c}{total_amount:.2f}**, but printed total is **{c}{printed_val:.2f}** "
            f"(Difference: **{c}{diff:.2f}**).\n\n"
            f"👉 Please review the table above to remove non-item lines (e.g. date, weight, or tender lines). "
            f"If this total is intentional, check **'Confirm save despite discrepancy'** below and click Save again."
        )

    final_total = printed_val if (printed_val and abs(total_amount - printed_val) <= 1.0) else total_amount

    receipt_id = save_receipt(
        store_name=store_name.strip() or "Unknown Store",
        items=items_to_save,
        scan_date=date_val.strip() or datetime.now().strftime("%Y-%m-%d"),
        grand_total=final_total,
        currency=c,
        payment_method=payment_method,
        notes=notes
    )

    return f"✅ **Saved successfully!** Receipt ID: `{receipt_id}` ({c}{final_total:,.2f}) recorded to Monthly Tracker."


# ─── Tab 2: Monthly Financial Analytics ──────────────────────────────────────
def load_monthly_dashboard(month_key: Optional[str] = None):
    """Generate summary metrics, MoM card, charts, and insights."""
    months = get_available_months()
    target_month = month_key
    if not target_month or target_month == "No data yet":
        target_month = months[0] if months else None

    if not target_month:
        empty_card = "<div class='stat-value'>₹0.00</div>"
        empty_count = "<div class='stat-value'>0</div>"
        return empty_card, empty_count, empty_card, "<div class='stat-value'>—</div>", None, "No transaction records available. Scan receipts in Tab 1 to populate analytics.", []

    data = get_monthly_data(target_month)
    if not data or not data.get("receipts"):
        c = "₹"
        return f"<div class='stat-value'>{c}0.00</div>", "<div class='stat-value'>0</div>", f"<div class='stat-value'>{c}0.00</div>", "<div class='stat-value'>—</div>", None, f"No transactions recorded for {target_month}.", []

    curr = data.get("currency", "₹")
    card_total = f"<div class='stat-value'>{curr}{data['grand_total']:,.2f}</div>"
    card_trips = f"<div class='stat-value'>{data['receipt_count']}</div>"
    card_avg = f"<div class='stat-value'>{curr}{data['avg_per_receipt']:,.2f}</div>"

    mom_val = data.get("mom_change_pct", 0.0)
    mom_icon = "📈 +" if mom_val > 0 else "📉 "
    card_mom = f"<div class='stat-value'>{mom_icon}{mom_val:.1f}%</div>" if data.get("prev_month") else "<div class='stat-value'>Base Month</div>"

    insights_list = generate_insights(data)
    formatted_insights = []
    for item in insights_list:
        if "Financial Health Score:" in item:
            formatted_insights.append(f"### 🛡️ Financial Health Status\n> **{item.split(':', 1)[1].strip()}**\n\n---")
        elif "Primary Expense Driver:" in item:
            formatted_insights.append(f"🛒 **Primary Expense Driver:** {item.split(':', 1)[1].strip()}")
        elif "Secondary Category Allocation:" in item:
            formatted_insights.append(f"📊 **Secondary Categories:** {item.split(':', 1)[1].strip()}")
        elif "Peak Line-Item Expenditure:" in item:
            formatted_insights.append(f"🏷️ **Peak Purchase:** {item.split(':', 1)[1].strip()}")
        elif "Basket Micro-Economics:" in item:
            formatted_insights.append(f"📦 **Basket Dynamics:** {item.split(':', 1)[1].strip()}")
        elif "Merchant Concentration:" in item:
            formatted_insights.append(f"🏪 **Merchant Concentration:** {item.split(':', 1)[1].strip()}")
        elif "Store Pricing Arbitrage:" in item:
            formatted_insights.append(f"⚖️ **Store Pricing Arbitrage:** {item.split(':', 1)[1].strip()}")
        elif "Run-Rate Forecast:" in item:
            formatted_insights.append(f"📈 **Run-Rate Projection:** {item.split(':', 1)[1].strip()}")
        elif "Actionable Savings Lever:" in item:
            formatted_insights.append(f"🎯 **Actionable Savings Lever:** {item.split(':', 1)[1].strip()}")
        elif "Budget Opportunity:" in item:
            formatted_insights.append(f"💡 **Budget Opportunity:** {item.split(':', 1)[1].strip()}")
        elif ":" in item:
            k, v = item.split(":", 1)
            formatted_insights.append(f"💡 **{k.strip()}:** {v.strip()}")
        else:
            formatted_insights.append(f"💡 {item}")
    insights_md = "\n\n".join(formatted_insights)

    receipt_rows = []
    for r in data.get("receipts", []):
        r_curr = r.get("currency", curr)
        receipt_rows.append([
            r.get("id", ""),
            r.get("date", ""),
            r.get("store", ""),
            f"{r_curr}{r.get('total', 0.0):,.2f}",
            len(r.get("items", []))
        ])

    chart_img = generate_monthly_chart(target_month)
    chart_pil = None
    if chart_img and os.path.exists(chart_img):
        try:
            with Image.open(chart_img) as im:
                chart_pil = im.copy()
        except Exception:
            chart_pil = None
    return card_total, card_trips, card_avg, card_mom, chart_pil, insights_md, receipt_rows


def refresh_month_choices():
    """Update dropdown list of available months."""
    months = get_available_months()
    if not months:
        return gr.Dropdown(choices=["No data yet"], value=None)
    return gr.Dropdown(choices=months, value=months[0])


def export_csv_action(month_key: str):
    if not month_key:
        return None
    return export_to_csv(month_key)


def export_json_action(month_key: str):
    if not month_key:
        return None
    return export_to_json(month_key)


# ─── Tab 3: Receipt Manager & CRUD ──────────────────────────────────────────
def search_and_list_receipts(search_term: str):
    records = list_receipts(search_query=search_term)
    rows = []
    for r in records:
        curr = r.get("currency", "₹")
        sample_items = ", ".join([it.get("item_name", "") for it in r.get("items", [])[:3]])
        if len(r.get("items", [])) > 3:
            sample_items += "..."
        rows.append([
            r.get("id", ""),
            r.get("date", ""),
            r.get("store", ""),
            f"{curr}{r.get('total', 0.0):,.2f}",
            sample_items
        ])
    return rows


def delete_receipt_action(receipt_id: str, confirmed: bool):
    if not receipt_id or not receipt_id.strip():
        return "⚠️ Please select a row above or enter a Receipt ID to delete.", search_and_list_receipts("")
    if not confirmed:
        return "⚠️ Confirmation required: Check 'Yes, I confirm permanent deletion' before proceeding.", search_and_list_receipts("")
    deleted = delete_receipt(receipt_id.strip())
    if deleted:
        msg = f"🗑️ Successfully deleted `{receipt_id.strip()}`."
    else:
        msg = f"⚠️ Receipt `{receipt_id.strip()}` not found."
    return msg, search_and_list_receipts("")


def on_select_repo_row(evt: gr.SelectData, df):
    """Auto-fill delete ID input when clicking any row in the repository table."""
    try:
        if isinstance(df, pd.DataFrame):
            return str(df.iloc[evt.index[0], 0])
        elif isinstance(df, list):
            return str(df[evt.index[0]][0])
    except Exception:
        pass
    return ""


# ─── Build Gradio Application ────────────────────────────────────────────────
CUSTOM_CSS = """
    .gradio-container { max-width: 1200px !important; margin: auto; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .stat-card { background: #1E293B; border: 1px solid #334155; border-radius: 10px; padding: 15px; text-align: center; }
    .stat-label { color: #94A3B8; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-value { font-size: 24px; font-weight: bold; color: #F8FAFC; margin-top: 5px; }
    .recon-badge { padding: 6px 12px; border-radius: 6px; font-weight: 600; font-size: 13px; background: #064E3B; color: #6EE7B7; display: inline-block; }
    footer { display: none !important; }
"""

with gr.Blocks(title="Smart Receipt Scanner & Expense Intelligence") as demo:

    gr.Markdown("""
    # 🧾 Smart Receipt Scanner & Expense Intelligence
    **Spatial OCR Pipeline • Multi-Currency • Transformer Categorization • Human-in-the-Loop Verification • Financial Analytics**
    """)

    with gr.Tabs() as tabs:

        # ── TAB 1: RECEIPT SCANNER & EDITOR ──────────────────────────────────
        with gr.Tab("Scan & Verify Receipt"):
            with gr.Row():
                with gr.Column(scale=5):
                    sample_picker = gr.Dropdown(
                        label="⚡ Quick Demo: Pick Sample Receipt",
                        choices=list(SAMPLE_RECEIPTS.keys()),
                        value=None,
                        info="Instant test without uploading your own photo"
                    )
                    image_input = gr.Image(label="Receipt Photo", type="numpy", height=380)

                    with gr.Row():
                        engine_select = gr.Radio(
                            label="OCR Engine",
                            choices=["Tesseract Spatial (Fast & Accurate)", "TrOCR (Deep Vision Transformer)"],
                            value="Tesseract Spatial (Fast & Accurate)"
                        )

                    scan_btn = gr.Button("🔍 Scan & Extract Receipt", variant="primary", size="lg")

                with gr.Column(scale=6):
                    gr.Markdown("### Extracted Metadata & Financial Summary")
                    with gr.Row():
                        store_name_in = gr.Textbox(label="Store / Merchant", placeholder="e.g. Woolworths, Supermart")
                        date_in = gr.Textbox(label="Receipt Date", placeholder="YYYY-MM-DD")
                        currency_in = gr.Dropdown(
                            label="Currency",
                            choices=["$", "₹", "€", "£", "¥"],
                            value="₹",
                            scale=1
                        )

                    with gr.Row():
                        subtotal_in = gr.Textbox(label="Subtotal", interactive=False)
                        tax_in = gr.Textbox(label="Tax (GST/VAT)", interactive=False)
                        total_in = gr.Textbox(label="Printed Total", interactive=False)

                    recon_status_box = gr.Textbox(label="Financial Reconciliation Status", interactive=False)
                    status_banner = gr.Markdown("Ready to scan receipt.")

            gr.Markdown("### 📝 Itemized Expenses (Editable — Adjust details before saving)")
            with gr.Row():
                items_editor = gr.DataFrame(
                    headers=["Item Name", "Category", "Amount", "Confidence", "Qty"],
                    datatype=["str", "str", "number", "str", "number"],
                    interactive=True,
                    wrap=True,
                    label="Line Items Table (Supports negative discount rows e.g. -2.50)"
                )

            with gr.Row():
                calc_total_display = gr.Markdown("**Calculated Items Total: ₹0.00**")
                recalc_btn = gr.Button("🔄 Recompute Total", size="sm")

            with gr.Row():
                pay_method_in = gr.Dropdown(
                    label="Payment Method",
                    choices=["Card", "UPI", "Cash", "Net Banking", "Other"],
                    value="Card"
                )
                notes_in = gr.Textbox(label="Transaction Notes (optional)", placeholder="e.g. Weekly grocery stockup")

            with gr.Row():
                force_save_chk = gr.Checkbox(
                    label="⚠️ Confirm save despite discrepancy (Check only if you intend to save an unbalanced receipt)",
                    value=False
                )

            save_db_btn = gr.Button("💾 Confirm & Save to Expenses", variant="primary", size="lg")
            save_feedback = gr.Markdown("")

            with gr.Accordion("🖼️ Visual OCR Inspector (Bounding Boxes & Layout)", open=False):
                annotated_preview = gr.Image(label="Annotated Image with Text Bounding Boxes", height=450)

            with gr.Accordion("📄 Raw OCR Transcript (Debug View)", open=False):
                raw_ocr_output = gr.Textbox(label="Raw Recognized Text", lines=8, interactive=False)

            # Event Handlers
            sample_picker.change(
                fn=load_sample_receipt,
                inputs=[sample_picker],
                outputs=[image_input]
            )

            scan_btn.click(
                fn=scan_receipt_action,
                inputs=[image_input, engine_select],
                outputs=[
                    store_name_in, date_in, subtotal_in, tax_in, total_in, currency_in, recon_status_box,
                    items_editor, annotated_preview, raw_ocr_output, status_banner
                ]
            ).then(
                fn=recompute_items_total,
                inputs=[items_editor, currency_in],
                outputs=[calc_total_display]
            )

            currency_in.change(
                fn=recompute_items_total,
                inputs=[items_editor, currency_in],
                outputs=[calc_total_display]
            )

            recalc_btn.click(
                fn=recompute_items_total,
                inputs=[items_editor, currency_in],
                outputs=[calc_total_display]
            )



        # ── TAB 2: FINANCIAL ANALYTICS & INSIGHTS ────────────────────────────
        init_months = get_available_months()
        init_month = init_months[0] if init_months else None
        init_total, init_trips, init_avg, init_mom, init_chart, init_insights, init_roster = load_monthly_dashboard(init_month)

        with gr.Tab("📊 Spending Analytics & AI Insights") as tab_spending_analytics:
            with gr.Row():
                month_select = gr.Dropdown(
                    label="Select Statement Month",
                    choices=init_months or ["No data yet"],
                    value=init_month,
                    scale=3
                )
                refresh_analytics_btn = gr.Button("🔄 Refresh Dashboard", scale=1)
                export_csv_btn = gr.Button("📥 Export CSV", scale=1)
                export_json_btn = gr.Button("📥 Export JSON", scale=1)

            # Metric Cards (Pre-populated with live data)
            with gr.Row():
                with gr.Column(elem_classes=["stat-card"]):
                    gr.Markdown("<div class='stat-label'>Total Monthly Spend</div>")
                    stat_total = gr.HTML(init_total)
                with gr.Column(elem_classes=["stat-card"]):
                    gr.Markdown("<div class='stat-label'>Shopping Trips</div>")
                    stat_trips = gr.HTML(init_trips)
                with gr.Column(elem_classes=["stat-card"]):
                    gr.Markdown("<div class='stat-label'>Average Ticket Size</div>")
                    stat_avg = gr.HTML(init_avg)
                with gr.Column(elem_classes=["stat-card"]):
                    gr.Markdown("<div class='stat-label'>Month-over-Month</div>")
                    stat_mom = gr.HTML(init_mom)

            with gr.Row():
                with gr.Column(scale=5):
                    gr.Markdown("### 💡 Rule-Based Spending Insights & Analytics")
                    insights_display = gr.Markdown(init_insights)

                with gr.Column(scale=7):
                    gr.Markdown("### 📈 Visual Breakdown (Donut & Ranked Bar)")
                    chart_display = gr.Image(value=init_chart, label="Spending Chart", height=380)

            gr.Markdown("### 📋 Monthly Receipts Roster")
            monthly_receipts_table = gr.DataFrame(
                headers=["Receipt ID", "Date", "Merchant", "Total Spend", "Items Count"],
                value=init_roster,
                interactive=False
            )

            file_download = gr.File(label="Download Exported File", visible=True)

            # ── Interactive AI Financial Advisor Q&A ────────────────────────
            with gr.Accordion("🤖 Ask AI Financial Advisor (Interactive Q&A)", open=True):
                with gr.Row():
                    advisor_query_in = gr.Textbox(
                        label="Ask anything about your spending, budget, or trends",
                        placeholder="e.g. 'What was my biggest expense?', 'Break down my spending by category', 'How can I save money this month?'",
                        scale=4
                    )
                    ask_advisor_btn = gr.Button("💬 Ask Advisor", variant="primary", scale=1)

                with gr.Row():
                    gr.Examples(
                        examples=[
                            ["What was my single most expensive purchase?"],
                            ["Break down my spending by category"],
                            ["Where do I spend the most money?"],
                            ["Give me 3 specific recommendations to cut my monthly expenses"],
                            ["What is my projected month-end spend at this rate?"],
                            ["How much did I spend on Food?"]
                        ],
                        inputs=[advisor_query_in]
                    )

                advisor_answer_box = gr.Markdown("💬 *Click any quick prompt above or type your own question to receive instant AI spending analysis.*")

                ask_advisor_btn.click(
                    fn=query_financial_advisor,
                    inputs=[advisor_query_in, month_select],
                    outputs=[advisor_answer_box]
                )
                advisor_query_in.submit(
                    fn=query_financial_advisor,
                    inputs=[advisor_query_in, month_select],
                    outputs=[advisor_answer_box]
                )

            # Tab & Control Events
            tab_spending_analytics.select(
                fn=load_monthly_dashboard,
                inputs=[month_select],
                outputs=[stat_total, stat_trips, stat_avg, stat_mom, chart_display, insights_display, monthly_receipts_table]
            )

            month_select.change(
                fn=load_monthly_dashboard,
                inputs=[month_select],
                outputs=[stat_total, stat_trips, stat_avg, stat_mom, chart_display, insights_display, monthly_receipts_table]
            )

            refresh_analytics_btn.click(
                fn=refresh_month_choices,
                outputs=[month_select]
            ).then(
                fn=load_monthly_dashboard,
                inputs=[month_select],
                outputs=[stat_total, stat_trips, stat_avg, stat_mom, chart_display, insights_display, monthly_receipts_table]
            )

            export_csv_btn.click(
                fn=export_csv_action,
                inputs=[month_select],
                outputs=[file_download]
            )

            export_json_btn.click(
                fn=export_json_action,
                inputs=[month_select],
                outputs=[file_download]
            )

        # ── TAB 3: RECEIPT REPOSITORY & CRUD ─────────────────────────────────
        with gr.Tab("🗄️ Receipt Repository & Management"):
            gr.Markdown("Search, inspect, and manage archived receipts.")

            with gr.Row():
                search_query_box = gr.Textbox(
                    label="Search Receipts",
                    placeholder="Search by merchant name or purchased item...",
                    scale=4
                )
                search_btn = gr.Button("Search", scale=1)

            repo_table = gr.DataFrame(
                headers=["Receipt ID", "Date", "Merchant", "Total Spend", "Sample Items"],
                value=search_and_list_receipts(""),
                interactive=False,
                wrap=True
            )

            with gr.Row():
                delete_id_box = gr.Textbox(label="Receipt ID to Remove (Click any row above to auto-select)", placeholder="e.g. REC-202610-001", scale=3)
                delete_confirm_chk = gr.Checkbox(label="⚠️ Yes, I confirm permanent deletion of this receipt", value=False, scale=2)
                delete_btn = gr.Button("🗑️ Delete Receipt", variant="stop", scale=1)

            delete_feedback = gr.Markdown("")

            repo_table.select(
                fn=on_select_repo_row,
                inputs=[repo_table],
                outputs=[delete_id_box]
            )

            search_btn.click(
                fn=search_and_list_receipts,
                inputs=[search_query_box],
                outputs=[repo_table]
            )

            delete_btn.click(
                fn=delete_receipt_action,
                inputs=[delete_id_box, delete_confirm_chk],
                outputs=[delete_feedback, repo_table]
            )

        # ── TAB 4: ARCHITECTURE & RESUME SPECS ───────────────────────────────
        with gr.Tab("📐 System Architecture & Specs"):
            gr.Markdown("""
            ## 🏗️ Technical Architecture & Pipeline Design

            ```
            ┌────────────────────────┐
            │  Receipt Image (Photo) │
            └───────────┬────────────┘
                        │
                        ▼
            ┌──────────────────────────────────────────────┐
            │  Computer Vision Preprocessing (OpenCV)      │
            │  • Dynamic Resolution Scaling (2.5x <1100px) │
            │  • Document Auto-Deskewing (Moments / Angle) │
            │  • Contrast Boost (CLAHE Clip=2.2, Grid 8x8) │
            │  • Bilateral Edge-Preserving Denoising       │
            └───────────┬──────────────────────────────────┘
                        │
                        ▼
            ┌──────────────────────────────────────────────┐
            │  Spatial Line Clustering & Transcription     │
            │  • Engine 1: Tesseract Spatial image_to_data │
            │  • Engine 2: Microsoft TrOCR Line-by-Line    │
            └───────────┬──────────────────────────────────┘
                        │
                        ▼
            ┌──────────────────────────────────────────────┐
            │  Semantic Information Extraction & Matching  │
            │  • Currency Auto-Detection ($, €, £, ₹, ¥)   │
            │  • Garbage Merchant Suppression              │
            │  • Weight/Scale Line Attachment to Products  │
            │  • Negative Discount Retention (-$15.00)     │
            │  • Date & Tender Rejection (CASH, CHANGE)    │
            │  • Financial Cross-Reconciliation Check      │
            └───────────┬──────────────────────────────────┘
                        │
                        ▼
            ┌──────────────────────────────────────────────┐
            │  Multi-Tier Hybrid Categorization Engine     │
            │  • Negative Filter (Tender/Weight -> Other)  │
            │  • Tier 1: Domain Brand/Keyword Map (<0.1ms) │
            │  • Tier 2: Fine-Tuned DistilBERT Transformer │
            │  • Tier 3: Semantic Cosine & Token Fallback  │
            └───────────┬──────────────────────────────────┘
                        │
                        ▼
            ┌──────────────────────────────────────────────┐
            │  Human-in-the-Loop Gradio UI & REST API      │
            │  • Interactive Line-Item Verification Editor │
            │  • Reconciliation Discrepancy Guard          │
            │  • Auto-Refreshing Analytics & MoM Dashboard │
            │  • FastAPI REST Endpoints with Swagger Docs  │
            └──────────────────────────────────────────────┘
            ```
            """)

    # ── Chained Save Event: Synchronizes Analytics & Repository ──────────────
    save_db_btn.click(
        fn=confirm_and_save_receipt,
        inputs=[store_name_in, date_in, items_editor, total_in, currency_in, pay_method_in, notes_in, force_save_chk],
        outputs=[save_feedback]
    ).then(
        fn=refresh_month_choices,
        outputs=[month_select]
    ).then(
        fn=load_monthly_dashboard,
        inputs=[month_select],
        outputs=[stat_total, stat_trips, stat_avg, stat_mom, chart_display, insights_display, monthly_receipts_table]
    ).then(
        fn=search_and_list_receipts,
        inputs=[search_query_box],
        outputs=[repo_table]
    )

    # ── Automatic Page Load Initialization ───────────────────────────────────
    # Populates dashboard and repository tables immediately on load
    demo.load(
        fn=load_monthly_dashboard,
        inputs=[month_select],
        outputs=[stat_total, stat_trips, stat_avg, stat_mom, chart_display, insights_display, monthly_receipts_table]
    )

# ─── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    share = os.environ.get("GRADIO_SHARE", "0") == "1"
    print(f"🚀 Launching Smart Receipt Scanner on http://localhost:{port}")
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=share,
        theme=gr.themes.Soft(primary_hue="emerald", secondary_hue="indigo"),
        css=CUSTOM_CSS,
        show_error=True,
        allowed_paths=[
            BASE_DIR,
            os.path.join(BASE_DIR, "receipts_db"),
            os.path.join(BASE_DIR, "sample_receipts")
        ]
    )