"""Minimal connectivity probe for model / provider edit dialogs."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from modules.model_catalog import join_openai_compat_url

PROBE_TIMEOUT_SEC = 8.0


def _headers(api_key: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Live2D-Suzu-probe",
    }
    key = str(api_key or "").strip()
    if key and not key.startswith("AIza"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _explain_status(status: int) -> str:
    if status == 401:
        return "Key 无效或未授权"
    if status == 403:
        return "没有权限，检查 Key 或套餐"
    if status == 404:
        return "地址或模型名找不到"
    if status == 429:
        return "请求太频繁，稍后再试"
    if 500 <= status <= 599:
        return "服务端出错"
    if status == 200:
        return "连接正常"
    return f"HTTP {status}"


def probe_model_endpoint(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Send a tiny request using unsaved dialog fields.

    Returns ok, latency_ms, status, message. Does not use chat_complete.
    """
    data = dict(cfg or {})
    base = str(data.get("base_url") or "").strip().rstrip("/")
    key = str(data.get("api_key") or "").strip()
    model = str(data.get("model") or "").strip()
    style = str(data.get("api_style") or "").strip().lower()
    if not base:
        return {
            "ok": False,
            "latency_ms": 0,
            "status": 0,
            "message": "还没有填写 Base URL",
        }

    t0 = time.perf_counter()
    try:
        if style in {"gemini_native", "google"} or (
            style == "gemini" and "generativelanguage.googleapis.com" in base
        ):
            status, body_ok = _probe_gemini(base, key, model)
        elif model:
            status, body_ok = _probe_chat(base, key, model, style)
        else:
            status, body_ok = _probe_models_list(base, key)
    except requests.Timeout:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "status": 0,
            "message": f"超时（{int(PROBE_TIMEOUT_SEC)}s）",
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "status": 0,
            "message": f"连不上：{exc.__class__.__name__}",
        }
    latency = int((time.perf_counter() - t0) * 1000)
    ok = 200 <= int(status) < 300 and body_ok
    return {
        "ok": ok,
        "latency_ms": latency,
        "status": int(status),
        "message": _explain_status(int(status)),
    }


def _probe_chat(base: str, key: str, model: str, style: str) -> tuple[int, bool]:
    if style in {"responses", "openai_responses"}:
        url = join_openai_compat_url(base, "responses")
        payload = {
            "model": model,
            "input": "ping",
            "max_output_tokens": 1,
        }
    else:
        url = join_openai_compat_url(base, "chat/completions")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    resp = requests.post(
        url,
        json=payload,
        headers=_headers(key),
        timeout=PROBE_TIMEOUT_SEC,
    )
    return resp.status_code, True


def _probe_models_list(base: str, key: str) -> tuple[int, bool]:
    try:
        url = join_openai_compat_url(base, "models")
    except ValueError:
        url = base.rstrip("/") + "/models"
    resp = requests.get(url, headers=_headers(key), timeout=PROBE_TIMEOUT_SEC)
    return resp.status_code, True


def _probe_gemini(base: str, key: str, model: str) -> tuple[int, bool]:
    import re

    cleaned = re.sub(r"/v\d+$", "", base, flags=re.IGNORECASE).rstrip("/")
    name = model or "gemini-2.0-flash"
    url = f"{cleaned}/v1beta/models/{name}:generateContent"
    headers = {"Content-Type": "application/json"}
    if key and key.startswith("AIza"):
        url = f"{url}?{urlencode({'key': key})}"
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 1},
    }
    resp = requests.post(
        url, json=payload, headers=headers, timeout=PROBE_TIMEOUT_SEC
    )
    return resp.status_code, True
