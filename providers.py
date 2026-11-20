"""Which model writes the glosses and translations. All three speak the OpenAI chat API.

Speech recognition is always Xiaomi MiMo (asr.py); only the annotation model is a choice.
Prices: USD per 1M tokens (input, cached input, output), from each provider's price page (Nov 2026).
DeepSeek bills peak-hour rates shown here and half off-peak.
"""

from __future__ import annotations

import os
import threading

PROVIDERS = {
    "openai": {
        "name": "OpenAI GPT", "model": "gpt-6-luna", "base_url": None, "key": "OPENAI_API_KEY",
        "price": (0.10, 0.01, 0.50),
        "note": "国内需要翻墙和海外付款方式",
    },
    "deepseek": {
        "name": "DeepSeek", "model": "deepseek-flash", "base_url": "https://api.deepseek.com", "key": "DEEPSEEK_API_KEY",
        "price": (0.30, 0.006, 1.20),
        "note": "国内可直接使用",
    },
    "mimo": {
        "name": "小米 MiMo", "model": "mimo-v2.6-flash", "base_url": "https://api.xiaomimimo.com/v1", "key": "MIMO_API_KEY",
        "price": (0.14, 0.014, 0.28),
        "note": "国内可直接使用；和语音识别共用一个 key",
    },
}
DEFAULT = "deepseek"

_clients: dict = {}
_lock = threading.Lock()


def current() -> str:
    p = os.environ.get("ANNOTATION_PROVIDER", "").strip().lower()
    if p in PROVIDERS:
        return p
    if os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANNOTATION_PROVIDER"):
        return "openai"           # older setups: only an OpenAI key in .env
    return DEFAULT


def model(provider: str | None = None) -> str:
    p = provider or current()
    return os.environ.get("OPENAI_MODEL") if p == "openai" and os.environ.get("OPENAI_MODEL") else PROVIDERS[p]["model"]


def has_key(provider: str | None = None) -> bool:
    return bool(os.environ.get(PROVIDERS[provider or current()]["key"]))


def client(provider: str | None = None):
    p = provider or current()
    key = os.environ.get(PROVIDERS[p]["key"], "")
    with _lock:
        c = _clients.get((p, key))
        if c is None:
            from openai import OpenAI
            base = os.environ.get(f"{p.upper()}_BASE_URL") or PROVIDERS[p]["base_url"]
            c = _clients[(p, key)] = OpenAI(api_key=key, base_url=base, max_retries=3, timeout=120)
        return c


def reset_clients():
    with _lock:
        _clients.clear()


def chat_options(model_name: str, effort: str | None) -> dict:
    """Per-provider way to say how hard to think. The server asks for: none (punctuation), low (lines
    near the viewer, translations) or medium (annotations further ahead).
    Measured on 96 subtitle lines (Nov 2026):
      GPT: reasoning_effort as asked.
      DeepSeek: thinking off ~3 s per batch; thinking on ~12 s, catches a few more context meanings
                -> only for "medium" (work ahead of the viewer).
      MiMo: thinking on ~80 s per batch, too slow for live use -> always off."""
    m = model_name.lower()
    if m.startswith(("gpt-5", "gpt-6", "o")) and effort:
        return {"reasoning_effort": effort}
    if m.startswith("deepseek"):
        if effort in ("medium", "high"):
            return {"reasoning_effort": "low", "extra_body": {"thinking": {"type": "enabled"}}}
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    if m.startswith("mimo"):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}


def cost_usd(provider: str, usage_total: dict) -> float:
    pin, pcache, pout = PROVIDERS[provider]["price"]
    cached = usage_total.get("cached_input", 0)
    return ((usage_total.get("input", 0) - cached) * pin + cached * pcache + usage_total.get("output", 0) * pout) / 1e6
