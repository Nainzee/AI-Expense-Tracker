import gradio as gr
from PIL import Image
from datetime import datetime
import pandas as pd

from ocr_pipeline import extract_receipt_items
from categorizer import categorize_items
from monthly_summary import (
    save_receipt,
    get_monthly_data,
    get_available_months,
    generate_monthly_chart,
    generate_insights,
    export_to_csv
)

# ─── Tab 1: Receipt Scanner ───────────────────────────────────────────────────
def scan_receipt(image, store_name, use_trocr, save_to_db):
    """
    Main scanning function called when user clicks 'Scan Receipt'.
    Returns: items_table, raw_text, status_message
    """
    if image is None:
        return None, "", "Please upload a receipt image."

    pil_image = Image.fromarray(image)

    # Step 1: Extract text using OCR
    try:
        items, raw_text = extract_receipt_items(pil_image, use_trocr=use_trocr)
    except Exception as e:
        return None, "", f"OCR Error: {str(e)}"

    if not items:
        return None, raw_text, "No items detected. Try a clearer image or switch OCR mode."

    # Step 2: Categorize items
    try:
        categorized = categorize_items(items)
    except Exception as e:
        return None, raw_text, f"Categorization Error: {str(e)}"

    # Step 3: Build DataFrame for display
    df = pd.DataFrame([{
        "Item":       item["item_name"],
        "Category":   item["category"],
        "Amount (₹)": f"₹{item['amount']:.2f}",
        "Confidence": f"{item['confidence']*100:.0f}%"
    } for item in categorized])

    # Add total row
    total = sum(item["amount"] for item in categorized)
    total_row = pd.DataFrame([{
        "Item":       "TOTAL",
        "Category":   "",
        "Amount (₹)": f"₹{total:.2f}",
        "Confidence": ""
    }])
    df = pd.concat([df, total_row], ignore_index=True)

    # Step 4: Save to database if requested
    status = f"Found {len(categorized)} items. Total: ₹{total:.2f}"
    if save_to_db:
        receipt_id = save_receipt(store_name or "Unknown Store", categorized)
        status += f" | Saved as {receipt_id}"

    return df, raw_text, status


# ─── Tab 2: Monthly Dashboard ─────────────────────────────────────────────────
def load_dashboard(month_key):
    """Load and display the monthly spending dashboard."""
    if not month_key:
        return "No month selected.", None, "No data.", []

    data = get_monthly_data(month_key)
    if not data:
        return f"No data for {month_key}.", None, "No receipts found.", []

    # Build summary text
    summary = f"""
**Month:** {data['month']}
**Total Spent:** ₹{data['grand_total']:.2f}
**Receipts Scanned:** {data['receipt_count']}
**Average per Trip:** ₹{data['avg_per_receipt']:.2f}
**Top Category:** {data['top_category']}

**Category Breakdown:**
"""
    for cat, amt in sorted(data["category_totals"].items(), key=lambda x: -x[1]):
        pct = amt / data["grand_total"] * 100
        summary += f"\n- {cat}: ₹{amt:.2f} ({pct:.0f}%)"

    # Generate chart
    chart_path = generate_monthly_chart(month_key)

    # Generate insights
    insights_list = generate_insights(data)
    insights_text = "\n\n".join([f"• {i}" for i in insights_list])

    # Build receipts table
    receipts_data = [[
        r["date"], r["store"], f"₹{r['total']:.2f}", len(r["items"])
    ] for r in data["receipts"]]

    return summary, chart_path, insights_text, receipts_data


def refresh_months():
    """Refresh the list of available months."""
    months = get_available_months()
    if not months:
        return gr.Dropdown(choices=["No data yet"], value=None)
    return gr.Dropdown(choices=months, value=months[0])


def export_csv(month_key):
    """Export monthly data to CSV."""
    if not month_key:
        return None
    path = export_to_csv(month_key)
    return path


# ─── Build Gradio UI ──────────────────────────────────────────────────────────
with gr.Blocks(
    title="Smart Receipt Scanner",
    theme=gr.themes.Soft(primary_hue="emerald", secondary_hue="blue"),
    css="""
        .gradio-container { max-width: 1000px !important; margin: auto; }
        .tab-nav button { font-size: 15px !important; padding: 10px 20px !important; }
        .total-row { font-weight: bold !important; }
        footer { display: none !important; }
    """
) as demo:

    gr.Markdown("""
    # Smart Expense Receipt Scanner
    **Scan receipts → Auto-categorize items → Track monthly spending**
    """)

    with gr.Tabs():

        # ── TAB 1: SCANNER ───────────────────────────────────────────────────
        with gr.Tab("Receipt Scanner"):
            gr.Markdown("Upload a receipt photo to extract and categorize all items.")

            with gr.Row():
                with gr.Column(scale=1):
                    image_input  = gr.Image(label="Receipt Photo", type="numpy", height=350)
                    store_input  = gr.Textbox(label="Store Name (optional)", placeholder="e.g. Big Bazaar")

                    with gr.Row():
                        trocr_toggle = gr.Checkbox(label="Use TrOCR (slower, more accurate)", value=True)
                        save_toggle  = gr.Checkbox(label="Save to monthly tracker", value=True)

                    scan_btn = gr.Button("Scan Receipt", variant="primary", size="lg")

                with gr.Column(scale=1):
                    items_table  = gr.DataFrame(
                        label="Extracted Items",
                        headers=["Item", "Category", "Amount (₹)", "Confidence"],
                        interactive=False
                    )
                    status_text  = gr.Textbox(label="Status", interactive=False)

            with gr.Accordion("Raw OCR Output (for debugging)", open=False):
                raw_text_box = gr.Textbox(label="Raw Text", lines=8, interactive=False)

            scan_btn.click(
                fn=scan_receipt,
                inputs=[image_input, store_input, trocr_toggle, save_toggle],
                outputs=[items_table, raw_text_box, status_text]
            )

        # ── TAB 2: MONTHLY DASHBOARD ─────────────────────────────────────────
        with gr.Tab("Monthly Dashboard"):
            gr.Markdown("View your aggregated spending across all scanned receipts.")

            with gr.Row():
                month_dropdown = gr.Dropdown(
                    label="Select Month",
                    choices=get_available_months() or ["No data yet"],
                    value=None,
                    scale=2
                )
                refresh_btn = gr.Button("Refresh", scale=1)
                export_btn  = gr.Button("Export CSV", scale=1)

            with gr.Row():
                with gr.Column(scale=1):
                    summary_box = gr.Markdown(label="Summary")
                    insights_box = gr.Textbox(
                        label="AI Insights",
                        lines=6,
                        interactive=False
                    )
                with gr.Column(scale=1):
                    chart_output = gr.Image(label="Spending Chart", height=400)

            receipts_table = gr.DataFrame(
                label="All Receipts This Month",
                headers=["Date", "Store", "Total", "Items"],
                interactive=False
            )

            csv_output = gr.File(label="Download CSV")

            month_dropdown.change(
                fn=load_dashboard,
                inputs=[month_dropdown],
                outputs=[summary_box, chart_output, insights_box, receipts_table]
            )

            refresh_btn.click(
                fn=refresh_months,
                outputs=[month_dropdown]
            )

            export_btn.click(
                fn=export_csv,
                inputs=[month_dropdown],
                outputs=[csv_output]
            )

        # ── TAB 3: HOW IT WORKS ───────────────────────────────────────────────
        with gr.Tab("How It Works"):
            gr.Markdown("""
            ## System Architecture

            ```
            Receipt Image (JPG/PNG)
                 │
                 ▼
            Image Preprocessing (OpenCV)
            - Grayscale conversion
            - Denoising
            - Sharpening
            - Adaptive thresholding
                 │
                 ▼
            TrOCR (Microsoft)  ──or──  Tesseract OCR
            VisionEncoderDecoder           (fallback)
            (Pretrained on printed text)
                 │
                 ▼
            Text Parsing (Regex)
            - Extract item names
            - Extract prices
            - Skip headers/totals
                 │
                 ▼
            DistilBERT Classifier (Fine-tuned)
            - Input: item name
            - Output: category + confidence
                 │
                 ▼
            Gradio UI
            - Items table
            - Monthly charts
            - AI insights
            - CSV export
            ```

            ## Models Used
            | Model | Purpose | Training |
            |-------|---------|----------|
            | TrOCR (microsoft/trocr-base-printed) | Extract text from images | Pretrained |
            | DistilBERT (distilbert-base-uncased) | Classify item categories | Fine-tuned by you |

            ## Categories
            Food • Drinks • Household • Personal Care • Medical • Bakery • Other
            """)

# ─── Launch ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Starting Smart Receipt Scanner...")
    print("Open http://localhost:7860 in your browser")
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=True,       # Set share=True to get a public URL
        show_error=True
    )