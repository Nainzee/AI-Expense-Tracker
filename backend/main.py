"""FastAPI application: REST API under /api/v1 plus the built React SPA at /."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import jwt

import storage
from backend import config, llm
from backend.routers import analytics, auth, budgets, export, insights, receipts, scan, splits, statements, transactions

log = logging.getLogger("backend")

app = FastAPI(title="Smart Expense Tracker API", version=config.VERSION, docs_url="/docs", redoc_url="/redoc")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def user_context_middleware(request: Request, call_next):
    auth_header = request.headers.get("Authorization")
    username = None
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        try:
            key = config.secret_key() or "smart-expense-tracker-jwt-secret-key-3.0-secure"
            payload = jwt.decode(token, key, algorithms=["HS256"])
            username = str(payload.get("sub", "")).strip() or None
        except Exception:
            username = None

    if not username and config.auth_disabled():
        username = "dev"

    storage.set_current_user(username)
    try:
        response = await call_next(request)
        return response
    finally:
        storage.set_current_user(None)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


api = APIRouter(prefix="/api/v1")


@api.get("/health", tags=["System"])
def health():
    models = llm.list_models()
    return {
        "status": "healthy",
        "version": config.VERSION,
        "timestamp": datetime.now().isoformat(),
        "auth_enabled": not config.auth_disabled(),
        "ollama": {
            "available": models is not None,
            "models": models or [],
            "vlm": config.ollama_vlm(),
            "text_model": config.ollama_text_model(),
        },
    }


@api.get("/categories", tags=["System"])
def categories():
    from categorizer import CANONICAL_CATEGORIES
    return {"categories": CANONICAL_CATEGORIES}


for r in (auth, scan, receipts, splits, statements, transactions, analytics, budgets, insights, export):
    api.include_router(r.router)
app.include_router(api)
app.add_api_route("/health", health, methods=["GET"], include_in_schema=False)

# ─── Production SPA (only when frontend/dist exists) ────────────────────────
_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isdir(_DIST):
    if os.path.isdir(os.path.join(_DIST, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = os.path.abspath(os.path.join(_DIST, path))
        if path and candidate.startswith(_DIST + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))
