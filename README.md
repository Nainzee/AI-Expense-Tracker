# Smart Expense Tracker

Upload a receipt or bill (photo or PDF). The app pulls out the merchant, date, line items and totals, sorts each
item into a category, and saves it all. From there you get dashboards, budgets and AI insights or chat about your
spending. Everything runs on your own machine at no cost.

## What it does

- **Extraction**: a local vision model (`qwen2.5vl:7b` via Ollama) reads the receipt while Tesseract OCR runs
  alongside it, and the two results are reconciled. Items are checked against the printed total, and any mismatch is
  flagged for you to review. When Ollama isn't running, the app falls back to Tesseract alone.
- **Categorization**: each item gets one of 15 canonical categories (Food, Drinks, Personal Care, Medical,
  Electronics, ...). You can edit any field before or after saving.
- **Storage**: receipts, items and the original files go into SQLite. Duplicate receipts are detected, and
  everything can be exported to CSV.
- **Analytics**: total and per-category spend, merchant breakdowns, top items, monthly trend, month-over-month
  change, and a month-end forecast.
- **Budgets**: monthly limits per category, with progress tracked against each limit.
- **AI insights and chat**: a local `qwen3:4b-instruct` writes monthly summaries and tips. It also answers questions
  like "How much did I spend on medical items in October?" by calling fixed, validated analytics tools, and it never
  writes SQL. When the model is unavailable, a rule-based advisor answers instead.
- **Web app**: React and Vite frontend, FastAPI backend, single-user JWT login.

## Setup

Requirements: Python 3.10+, Node 20+, Tesseract, and optionally Ollama.

```bash
# 1. Local AI (optional but recommended; about 8.5 GB of models)
brew install tesseract ollama
brew services start ollama          # or: ollama serve
ollama pull qwen2.5vl:7b            # receipt extraction (vision)
ollama pull qwen3:4b-instruct       # insights + chat (tool calling)

# 2. Backend on http://localhost:8000 (API docs at /docs)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export APP_USERNAME=admin APP_PASSWORD='choose-a-password' APP_SECRET_KEY="$(openssl rand -hex 32)"
uvicorn api:app --port 8000

# 3a. Frontend dev server on http://localhost:5173 (hot reload, proxies /api to :8000)
cd frontend && npm install && npm run dev

# 3b. Or build once; FastAPI then serves the app at http://localhost:8000/
cd frontend && npm install && npm run build
```

Tests: `python -m pytest`. Ollama is mocked, so no network is needed. Frontend type check and build: `npm run build`.

## Environment variables

See `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `APP_USERNAME`, `APP_PASSWORD` | unset | Login credentials. Requests are rejected until these are set. |
| `APP_SECRET_KEY` | unset | JWT signing key. Use a long random string. |
| `AUTH_DISABLED` | `0` | Set to `1` to skip login, for local development only. It issues a token to anyone, so never use it on a non-loopback host. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_VLM` | `qwen2.5vl:7b` | Vision model for extraction |
| `OLLAMA_TEXT_MODEL` | `qwen3:4b-instruct` | Model for insights and chat. Avoid thinking-only models such as `qwen3:4b`. |
| `OLLAMA_TIMEOUT_S` | `120` | Per-request timeout for the LLM |
| `RECEIPTS_DB_PATH` | `receipts_db/receipts.db` | SQLite file. Uploads are stored in `receipts_db/files/`. |

## Privacy

Everything stays local. The backend's only outbound calls go to `OLLAMA_URL` (localhost by default), and no cloud AI
APIs are used. Receipts, images and the database stay on your disk.

## Limitations

- **Speed**: extraction takes about 14–18 s per receipt on an Apple Silicon laptop with the 7B vision model. The
  Tesseract-only fallback is fast but less accurate on faint or busy receipts.
- **Hardware**: the models need about 8 GB of free RAM. Smaller machines should use only the Tesseract fallback.
- **Accuracy**: extraction was validated on four synthetic sample receipts, not on a broad real-world set.
  Crumpled, handwritten or non-English receipts may need manual correction in the editor.
- **AI quality**: insights and chat come from a 4B local model. They are useful but can be imprecise, so use the
  analytics numbers as the source of truth.
- **Scope**: single user, no bank sync, and PDFs are limited to 5 pages.

## Legacy

The earlier Gradio demo (`app.py`), along with its DistilBERT categorizer training (`train.py`) and evaluation
scripts (`eval/`), is still in the repo but is not part of the v3 app.

## License

MIT, see `LICENSE`.
