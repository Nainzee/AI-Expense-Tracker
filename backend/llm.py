"""Minimal Ollama REST client (local, free). Uses httpx; no SDK."""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from PIL import Image

from backend import config

log = logging.getLogger(__name__)

_cache: Dict[str, Any] = {"t": 0.0, "url": None, "models": None}


class LLMError(RuntimeError):
    pass


def list_models(force: bool = False) -> Optional[List[str]]:
    """Installed model names, or None if Ollama is unreachable. Cached ~10 s."""
    url = config.ollama_url()
    if not force and _cache["url"] == url and time.time() - _cache["t"] < 10:
        return _cache["models"]
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=2.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception:
        models = None
    _cache.update(t=time.time(), url=url, models=models)
    return models


def _has(models: List[str], name: str) -> bool:
    return any(m == name or m == f"{name}:latest" for m in models)


def is_available(model: Optional[str] = None) -> bool:
    """True when Ollama answers and the requested model (default: VLM) is installed."""
    models = list_models()
    if models is None:
        return False
    return _has(models, model or config.ollama_vlm())


def image_to_b64(img: Image.Image, max_side: int = 1600) -> str:
    img = img.convert("RGB")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _post_chat(payload: dict) -> dict:
    try:
        resp = httpx.post(f"{config.ollama_url()}/api/chat", json=payload, timeout=config.ollama_timeout())
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException as exc:
        raise LLMError("LLM request timed out") from exc
    except Exception as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc


def strip_thinking(text: str) -> str:
    """Drop any reasoning a thinking model leaks into content (``<think>…</think>``, opening tag optional)."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def chat(model: str, messages: List[dict], schema: Optional[dict] = None) -> str:
    """Single chat completion; with ``schema`` Ollama constrains output to that JSON schema."""
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                               "options": {"temperature": 0}}
    if schema is not None:
        payload["format"] = schema
    return strip_thinking(_post_chat(payload).get("message", {}).get("content", ""))


def chat_tools(model: str, messages: List[dict], tools: List[dict]) -> dict:
    """Chat with tool definitions; returns the assistant message (may contain ``tool_calls``)."""
    payload = {"model": model, "messages": messages, "tools": tools, "stream": False,
               "options": {"temperature": 0}}
    message = _post_chat(payload).get("message", {})
    message["content"] = strip_thinking(message.get("content", ""))
    return message
