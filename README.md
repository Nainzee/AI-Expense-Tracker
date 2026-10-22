# 💰 Smart Expense Tracker & Financial Intelligence

[![Live Demo](https://img.shields.io/badge/Live%20Demo-expenzetracker.site-6366F1?style=for-the-badge&logo=googlechrome&logoColor=white)](https://expenzetracker.site)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0%2B-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-8.0-646CFF?style=for-the-badge&logo=vite&logoColor=white)](https://vitejs.dev/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-4.0-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com/)
[![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

> An AI-powered, privacy-first personal finance platform. Scan receipts with multimodal vision, import and categorize bank statements, split bills with friends, forecast spending, and chat with a private local financial advisor — built with per-user isolated storage.

🌐 **Production Deployment**: **[https://expenzetracker.site](https://expenzetracker.site)**  
📖 **Interactive API Documentation**: **[https://expenzetracker.site/docs](https://expenzetracker.site/docs)**

---

## 🌟 Key Features

### 🧾 1. Intelligent Receipt Scanner (Dual-Engine Vision + OCR)
* **Hybrid Pipeline**: Runs a local vision-language model (`qwen2.5vl:7b` via Ollama) in parallel with **Tesseract OCR** for automated cross-reconciliation.
* **Arithmetic Validation**: Line-item amounts are summed and checked against printed totals; discrepancies are flagged for manual review.
* **Automatic Categorization**: Categorizes line items into 15 canonical spending categories (Food, Dining, Personal Care, Utilities, Electronics, etc.).
* **Active Learning Memory**: User corrections are remembered to continuously improve classification accuracy.

### 🏦 2. Bank & UPI Statement Import
* **Format Support**: Upload statements in **CSV**, **Excel (.xlsx, .xls)**, and **PDF** formats.
* **Preset & Custom Mappers**: Built-in support for HDFC Bank, ICICI Bank, PhonePe, and a dynamic interactive column mapper for generic statements.
* **Smart Reconciliation**: Detects duplicate transactions, flags unlinked payments, identifies recurring subscriptions, and matches bank debits against scanned receipts.

### 👥 3. Bill Splitting & Group Balances
* **Flexible Splitting**: Split receipts equally, by percentage, exact amounts, shares, or item-by-item assignment.
* **Balance Ledger**: Automatically tracks who owes you and whom you owe, with integrated settlement recording.

### 🔒 4. Multi-Tenant User Isolation & Security
* **Isolated SQLite Databases**: Each user account gets a physically isolated database (`receipts_db/users/<username>.db`). No shared state, zero cross-user data leakage.
* **Cryptographic Password Hashing**: Passwords stored using **PBKDF2-HMAC-SHA256** with 100,000 iterations and unique cryptographic salts.
* **Stateless JWT Sessions**: Secure token-based authorization for all protected REST endpoints.

### 📊 5. Comprehensive Financial Analytics & Budgets
* **Real-time Metrics**: Total spending, average payment size, month-over-month trends, category breakdowns, and top merchant leaderboards.
* **Predictive Forecasts**: Linear run-rate projection for month-end spend based on daily velocity.
* **Budget Limits**: Customizable category limits with visual progress bars and warning thresholds (75% warning, 100% exceeded).

### 🤖 6. Private Local AI Financial Advisor
* **Zero Cloud Dependence**: Powered by local `qwen3:4b-instruct` through Ollama. Your financial data never leaves your infrastructure.
* **Deterministic Tool Calling**: The AI model answers complex natural language spending questions by invoking predefined, validated analytics functions — never by generating unverified SQL queries.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React 19 + Vite SPA                      │
│        (Tailwind CSS, Lucide Icons, Recharts, React Query)   │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS / REST
┌──────────────────────────────▼──────────────────────────────┐
│                      Nginx Reverse Proxy                    │
│            (SSL Termination, SPA Routing, Caching)          │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                    FastAPI Backend (Python)                 │
│      ├── User Auth & Middleware (PBKDF2 + JWT)              │
│      ├── Receipt OCR Engine (Qwen2.5-VL / Tesseract)        │
│      ├── Statement Import & Deduplication Engine            │
│      ├── Split Ledger & Settlement Service                  │
│      └── Spending Analytics & Forecast Engine               │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
┌──────────────▼─────────────┐ ┌──────────────▼───────────────┐
│     Central Auth DB        │ │   Per-User Isolated DBs      │
│   (receipts_db/auth.db)    │ │ (receipts_db/users/*.db)     │
│   - users table            │ │ - receipts, line_items       │
│   - password hashes & salts│ │ - statements, transactions   │
│                            │ │ - budgets, splits, cache     │
└────────────────────────────┘ └──────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Domain | Technologies |
|---|---|
| **Frontend** | React 19, TypeScript, Vite 8, Tailwind CSS 4, React Router 7, TanStack Query, Recharts, Lucide React |
| **Backend** | Python 3.10+, FastAPI, Uvicorn, Pydantic v2, PyJWT, PyPDFium2, pdfplumber, openpyxl, OpenCV |
| **AI / OCR** | Ollama (`qwen2.5vl:7b`, `qwen3:4b-instruct`), Tesseract OCR, PyTorch (CPU-optimized) |
| **Database** | SQLite3 (ACID-compliant, per-user isolated files, automatic schema migrations) |
| **DevOps** | Nginx, Systemd, Certbot (Let's Encrypt SSL), Docker, Docker Compose, AWS Lightsail |

---

## 🚀 Getting Started (Local Development)

### Prerequisites
* **Python 3.10+**
* **Node.js 20+** and **npm**
* **Tesseract OCR**:
  * macOS: `brew install tesseract`
  * Ubuntu/Debian: `sudo apt-get install tesseract-ocr tesseract-ocr-eng libgl1`
* *(Optional)* **Ollama** for local AI models (`ollama serve`).

---

### 1. Clone the Repository
```bash
git clone https://github.com/Nainzee/AI-Expense-Tracker.git
cd AI-Expense-Tracker
```

### 2. Backend Setup
```bash
# Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies (CPU-optimized PyTorch recommended)
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env

# Run the FastAPI server
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
*API will be live at `http://localhost:8000` with Swagger docs at `http://localhost:8000/docs`.*

---

### 3. Frontend Setup
```bash
cd frontend

# Install packages
npm install

# Start Vite development server (proxies /api to :8000)
npm run dev
```
*Frontend will be running at `http://localhost:5173`.*

---

### 4. (Optional) Local AI Models Setup
To enable local vision extraction and AI spending chat:
```bash
# Install and start Ollama
brew install ollama
ollama serve

# Pull required models
ollama pull qwen2.5vl:7b        # Vision extraction
ollama pull qwen3:4b-instruct   # Financial chat & tool calling
```
*When Ollama is offline, the app seamlessly falls back to Tesseract OCR and rule-based insights.*

---

## ⚙️ Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Port for the backend application server |
| `HOST` | `0.0.0.0` | Host bind address |
| `APP_SECRET_KEY` | *(generated)* | Secret key for signing JWT tokens |
| `AUTH_DISABLED` | `0` | Set to `1` to bypass authentication (local dev only) |
| `DEFAULT_OCR_ENGINE` | `tesseract` | Primary OCR engine (`tesseract` or `trocr`) |
| `RECEIPTS_DB_PATH` | `receipts_db/receipts.db` | Base path for SQLite storage directory |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_VLM` | `qwen2.5vl:7b` | Ollama model for receipt image extraction |
| `OLLAMA_TEXT_MODEL` | `qwen3:4b-instruct` | Ollama model for financial chat & summaries |
| `OLLAMA_TIMEOUT_S` | `120` | Timeout in seconds for LLM inference |

---

## 📂 Project Structure

```
AI-Expense-Tracker/
├── backend/                  # FastAPI Application Core
│   ├── routers/              # Modular API Endpoints
│   │   ├── analytics.py      # Spending totals, forecasts & trends
│   │   ├── auth.py           # User signup, login & session info
│   │   ├── budgets.py        # Category limits & tracking
│   │   ├── receipts.py       # Receipt CRUD & line items
│   │   ├── scan.py           # Image/PDF upload & OCR scan
│   │   ├── splits.py         # Bill splitting & settlements
│   │   ├── statements.py     # Bank/UPI statement ingestion
│   │   └── transactions.py   # Transaction management & filters
│   ├── statements/           # Statement parsing & categorization engine
│   ├── auth.py               # PBKDF2 hashing, JWT encode/decode
│   ├── config.py             # Environment configuration
│   ├── db.py                 # SQLite schema migrations (v1 - v5)
│   ├── llm.py                # Ollama integration & function calling
│   └── main.py               # FastAPI entrypoint & static SPA mounting
├── frontend/                 # React 19 Single Page Application
│   ├── src/
│   │   ├── components/       # Reusable UI & modal components
│   │   ├── lib/              # Typed API client & state utilities
│   │   ├── pages/            # Dashboard, Receipts, Statements, Splits, etc.
│   │   ├── App.tsx           # App routing & Auth guards
│   │   └── main.tsx          # React application root
│   ├── package.json
│   └── vite.config.ts
├── receipts_db/              # Database Storage Directory
│   ├── auth.db               # Centralized credentials database
│   ├── users/                # Per-user isolated SQLite databases (*.db)
│   └── files/                # Uploaded receipt photos & PDF files
├── sample_receipts/          # Sample images for testing & validation
├── sample_statements/        # Sample CSV/XLSX/PDF bank statements
├── tests/                    # Automated pytest suite
├── Dockerfile                # Multi-stage production container
├── docker-compose.yml        # Docker service definition
└── requirements.txt          # Python dependency specifications
```

---

## 🧪 Testing

Run the automated backend test suite with PyTest:
```bash
# Run all tests
pytest tests/ -v

# Run specific API & multi-user isolation tests
pytest tests/test_v3_api.py -v
```

All 6 core verification suites pass with full mock coverage for LLM services and file processing.

---

## 🚢 Production Deployment (AWS Lightsail / Linux VPS)

The project includes production configurations for **Nginx**, **Systemd**, and **Certbot (SSL)**.

1. **Systemd Service** (`/etc/systemd/system/expense-tracker.service`):
   ```ini
   [Unit]
   Description=AI Expense Tracker FastAPI Backend
   After=network.target

   [Service]
   User=ubuntu
   WorkingDirectory=/home/ubuntu/AI-Expense-Tracker
   EnvironmentFile=/home/ubuntu/AI-Expense-Tracker/.env
   ExecStart=/home/ubuntu/AI-Expense-Tracker/venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

2. **Nginx with SSL Termination**:
   * Serves pre-built Vite assets (`frontend/dist`) directly with SPA URL fallback (`try_files $uri $uri/ /index.html;`).
   * Reverse-proxies `/api/` requests to `http://127.0.0.1:8000/api/` with `client_max_body_size 50M`.
   * Enforces automatic HTTP to HTTPS redirection via Let's Encrypt.

---

## 📄 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for complete details.

---

<p align="center">
  Crafted with ❤️ by <a href="https://github.com/Nainzee">Nainzee</a>
</p>
