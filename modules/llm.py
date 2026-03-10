# modules/llm.py
import threading
import time
import uuid
from typing import AsyncGenerator
from urllib.parse import urlencode

import requests
from openai import AsyncOpenAI, OpenAI

from config import LLM_ROUTER, MODELS, SENSOR_VISION_MODEL

try:
    from modules.model_transport_state import (
        get_preferred_transport,
        record_failure,
        record_success,
    )
except Exception:
    # Transport memory is best-effort and must not block LLM calls.
    def get_preferred_transport(model_key: str):
        return None

    def record_success(model_key: str, transport: str):
        return False

    def record_failure(model_key: str, transport: str, error: str = ""):
        return False


_LOG_LOCK = threading.Lock()
_METRIC_LOCK = threading.Lock()
_METRICS = []
_MAX_METRICS = 300


def _trace_log(*lines):
    with _LOG_LOCK:
        for line in lines:
            print(line)


def _record_metric(entry: dict):
    with _METRIC_LOCK:
        _METRICS.append(entry)
        if len(_METRICS) > _MAX_METRICS:
            del _METRICS[: len(_METRICS) - _MAX_METRICS]


def get_recent_llm_metrics(limit: int = 50):
    with _METRIC_LOCK:
        n = max(1, int(limit))
        return list(_METRICS[-n:])


def _model_style(config: dict) -> str:
    return str((config or {}).get("api_style", "")).strip().lower()


def _is_gemini_model(config: dict) -> bool:
    model_name = str((config or {}).get("model", "")).lower()
    return "gemini" in model_name


def _extract_text_content(raw_content) -> str:
    if isinstance(raw_content, str):
        return raw_content.strip()
    if isinstance(raw_content, list):
        parts = []
        for item in raw_content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _messages_to_responses_input(messages_context) -> list:
    output = []
    for msg in (messages_context or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user")).strip().lower() or "user"
        if role not in {"system", "user", "assistant"}:
            role = "user"
        text = _extract_text_content(msg.get("content", ""))
        if text:
            output.append({"role": role, "content": text})
    return output or [{"role": "user", "content": "你好"}]


def _messages_to_text_block(messages_context) -> str:
    lines = []
    for msg in (messages_context or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user")).strip().lower() or "user"
        text = _extract_text_content(msg.get("content", ""))
        if not text:
            continue
        lines.append(f"{role}: {text}")
    return "\n\n".join(lines).strip() or "user: 你好"


def _build_openai_compat_url(base_url: str, endpoint: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("openai compatible call missing base_url")
    ep = str(endpoint or "").strip().lstrip("/")
    if not ep:
        raise ValueError("openai compatible call missing endpoint")
    if base.endswith("/v1"):
        return f"{base}/{ep}"
    return f"{base}/v1/{ep}"


def _extract_responses_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""

    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    texts = []
    output = data.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
                out_text = part.get("output_text")
                if isinstance(out_text, str) and out_text.strip():
                    texts.append(out_text.strip())

    if not texts:
        choices = data.get("choices", [])
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message", {}) if isinstance(first, dict) else {}
            content = message.get("content", "") if isinstance(message, dict) else ""
            plain = _extract_text_content(content)
            if plain:
                texts.append(plain)

    return "\n".join(texts).strip()


def _chat_with_openai_responses(messages_context, config: dict, timeout: int = 30) -> str:
    model_name = str((config or {}).get("model", "")).strip()
    api_key = str((config or {}).get("api_key", "")).strip()
    base_url = str((config or {}).get("base_url", "")).strip()
    if not model_name:
        raise ValueError("responses call missing model")
    if not base_url:
        raise ValueError("responses call missing base_url")

    url = _build_openai_compat_url(base_url, "responses")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model_name,
        "input": _messages_to_responses_input(messages_context),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        fallback_payload = {
            "model": model_name,
            "input": _messages_to_text_block(messages_context),
        }
        fallback_resp = requests.post(url, headers=headers, json=fallback_payload, timeout=timeout)
        if fallback_resp.status_code >= 400:
            raise RuntimeError(f"openai_responses HTTP {fallback_resp.status_code}: {fallback_resp.text[:280]}")
        resp = fallback_resp
    data = resp.json()
    text = _extract_responses_text(data)
    if text:
        return text
    raise RuntimeError("openai_responses returned empty content")


def _build_gemini_native_url(base_url: str, model_name: str, api_key: str) -> tuple[str, dict]:
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    if not base:
        base = "https://generativelanguage.googleapis.com"
    url = f"{base}/v1beta/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.startswith("AIza"):
        url = f"{url}?{urlencode({'key': api_key})}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return url, headers


def _messages_to_gemini_contents(messages_context) -> list:
    contents = []
    system_chunks = []
    for msg in (messages_context or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user")).strip().lower()
        text = _extract_text_content(msg.get("content", ""))
        if not text:
            continue
        if role == "system":
            system_chunks.append(text)
            continue
        g_role = "model" if role == "assistant" else "user"
        contents.append({"role": g_role, "parts": [{"text": text}]})

    if system_chunks:
        sys_text = "\n\n".join(system_chunks)
        if contents:
            first = contents[0]
            first_text = ""
            parts = first.get("parts", [])
            if isinstance(parts, list) and parts and isinstance(parts[0], dict):
                first_text = str(parts[0].get("text", ""))
            first["parts"] = [{"text": f"[System Instruction]\n{sys_text}\n\n{first_text}"}]
        else:
            contents.append({"role": "user", "parts": [{"text": f"[System Instruction]\n{sys_text}"}]})

    return contents or [{"role": "user", "parts": [{"text": "你好"}]}]


def _extract_gemini_text(data: dict) -> str:
    try:
        cands = data.get("candidates", [])
        if not cands:
            return ""
        content = (cands[0] or {}).get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []
        texts = []
        for p in parts:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str) and t:
                    texts.append(t)
        return "\n".join(texts).strip()
    except Exception:
        return ""


def _chat_with_gemini_native(messages_context, config: dict, timeout: int = 30) -> str:
    model_name = str((config or {}).get("model", "")).strip()
    api_key = str((config or {}).get("api_key", "")).strip()
    base_url = str((config or {}).get("base_url", "")).strip()
    if not model_name:
        raise ValueError("gemini native call missing model")
    url, headers = _build_gemini_native_url(base_url, model_name, api_key)
    payload = {
        "contents": _messages_to_gemini_contents(messages_context),
        "generationConfig": {"temperature": 0.7},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"gemini_native HTTP {resp.status_code}: {resp.text[:280]}")
    data = resp.json()
    text = _extract_gemini_text(data)
    if text:
        return text
    raise RuntimeError("gemini_native returned empty content")


def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _build_attempt_order(config: dict, model_key: str = "") -> list[str]:
    style = _model_style(config)
    is_gemini = _is_gemini_model(config)

    if style in {"responses", "openai_responses"}:
        base = ["openai_responses", "openai", "gemini_native"] if is_gemini else ["openai_responses", "openai"]
    elif style in {"gemini_native", "google"}:
        base = ["gemini_native", "openai", "openai_responses"]
    elif style in {"openai", "gemini"}:
        base = ["openai", "openai_responses", "gemini_native"] if is_gemini else ["openai", "openai_responses"]
    else:
        base = ["openai", "openai_responses", "gemini_native"] if is_gemini else ["openai", "openai_responses"]

    attempts = _dedupe(base)
    preferred = get_preferred_transport(model_key) if model_key else None
    if preferred and preferred in attempts:
        attempts = [preferred] + [x for x in attempts if x != preferred]
    return attempts


async def analyze_image(
    image_base64: str,
    prompt: str = "请详细描述这张图片的内容。",
    model_name: str = None,
) -> str:
    target_key = model_name if model_name else (SENSOR_VISION_MODEL or "default")
    config = MODELS.get(target_key)
    if not config:
        return f"（视觉配置错误：找不到模型 {target_key}）"

    print(f"[Vision] 调用模型: {target_key}")

    try:
        client = AsyncOpenAI(api_key=config["api_key"], base_url=config["base_url"])
        response = await client.chat.completions.create(
            model=config["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }
            ],
            max_tokens=500,
            timeout=30,
        )
        content = getattr(response.choices[0].message, "content", "")
        if content:
            return str(content)
    except Exception as e:
        return f"（视觉识别失败: {e}）"

    return "（视觉识别返回为空）"


async def chat_with_ai_stream(messages_context, task_type="default") -> AsyncGenerator[str, None]:
    model_keys = LLM_ROUTER.get(task_type, LLM_ROUTER.get("default", []))
    if isinstance(model_keys, str):
        model_keys = [model_keys]
    if not model_keys:
        yield "（配置错误：无可用模型）"
        return

    for idx, key in enumerate(model_keys, 1):
        config = MODELS.get(key)
        if not config:
            continue

        print(f"[LLM Stream] 尝试 {idx}/{len(model_keys)}: {key}")
        yielded_any = False
        t0 = time.time()
        attempts = _build_attempt_order(config, key)
        preferred = get_preferred_transport(key)
        print(f"[LLM Stream] 传输顺序 model={key}: {attempts} preferred={preferred or '-'}")

        for method in attempts:
            try:
                if method == "openai":
                    client = AsyncOpenAI(api_key=config["api_key"], base_url=config["base_url"])
                    response = await client.chat.completions.create(
                        model=config["model"],
                        messages=messages_context,
                        stream=True,
                        timeout=20,
                    )
                    async for chunk in response:
                        delta = getattr(chunk.choices[0], "delta", None)
                        content = getattr(delta, "content", None) if delta else None
                        if content:
                            yielded_any = True
                            yield content
                elif method == "openai_responses":
                    text = _chat_with_openai_responses(messages_context, config, timeout=20)
                    if text:
                        yielded_any = True
                        yield text
                elif method == "gemini_native":
                    text = _chat_with_gemini_native(messages_context, config, timeout=20)
                    if text:
                        yielded_any = True
                        yield text
                else:
                    raise RuntimeError(f"unsupported transport: {method}")

                record_success(key, method)
                _record_metric(
                    {
                        "ts": time.time(),
                        "mode": "stream",
                        "task_type": task_type,
                        "model_key": key,
                        "transport": method,
                        "success": True,
                        "duration_ms": int((time.time() - t0) * 1000),
                        "error": "",
                    }
                )
                return
            except Exception as e:
                record_failure(key, method, str(e))
                print(f"[LLM Stream] 失败: {e} (model={key}, transport={method})")
                _record_metric(
                    {
                        "ts": time.time(),
                        "mode": "stream",
                        "task_type": task_type,
                        "model_key": key,
                        "transport": method,
                        "success": False,
                        "duration_ms": int((time.time() - t0) * 1000),
                        "error": str(e)[:300],
                    }
                )
                if yielded_any:
                    return
                continue

    yield "（所有模型连接失败，请检查网络或 Key）"


def chat_with_ai(messages_context, task_type="default", caller: str = "", request_id: str = ""):
    request_id = request_id or uuid.uuid4().hex[:8]
    caller = caller or "unknown"
    trace = f"task={task_type} caller={caller} req={request_id} tid={threading.get_ident()}"

    msg_count = len(messages_context) if isinstance(messages_context, list) else 0
    msg_chars = 0
    if isinstance(messages_context, list):
        for msg in messages_context:
            if not isinstance(msg, dict):
                continue
            msg_chars += len(_extract_text_content(msg.get("content", "")))

    _trace_log(
        f"\n{'=' * 40}",
        f"[LLM Sync] {trace}",
        f"[LLM Sync] payload messages={msg_count} chars~{msg_chars} ({trace})",
    )

    model_keys = LLM_ROUTER.get(task_type, LLM_ROUTER.get("default", []))
    if isinstance(model_keys, str):
        model_keys = [model_keys]

    for key_idx, key in enumerate(model_keys, 1):
        _trace_log(f"[LLM Sync] 尝试 #{key_idx}: {key} ({trace})")
        config = MODELS.get(key)
        if not config:
            continue

        t0 = time.time()
        attempts = _build_attempt_order(config, key)
        preferred = get_preferred_transport(key)
        _trace_log(f"[LLM Sync] transport_order={attempts} preferred={preferred or '-'} ({trace})")

        for method in attempts:
            try:
                if method == "openai":
                    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
                    response = client.chat.completions.create(
                        model=config["model"],
                        messages=messages_context,
                        timeout=30,
                    )
                    raw_content = getattr(response.choices[0].message, "content", "")
                    content = _extract_text_content(raw_content)
                elif method == "openai_responses":
                    content = _chat_with_openai_responses(messages_context, config, timeout=30)
                elif method == "gemini_native":
                    content = _chat_with_gemini_native(messages_context, config, timeout=30)
                else:
                    raise RuntimeError(f"unsupported transport: {method}")

                if not content:
                    raise RuntimeError(f"empty content from transport={method}")

                record_success(key, method)
                _record_metric(
                    {
                        "ts": time.time(),
                        "mode": "sync",
                        "task_type": task_type,
                        "caller": caller,
                        "model_key": key,
                        "transport": method,
                        "success": True,
                        "duration_ms": int((time.time() - t0) * 1000),
                        "error": "",
                    }
                )
                _trace_log(f"[LLM Sync] ✅ 成功({method}) (len={len(content)}) ({trace})")
                return str(content)
            except Exception as e:
                record_failure(key, method, str(e))
                _record_metric(
                    {
                        "ts": time.time(),
                        "mode": "sync",
                        "task_type": task_type,
                        "caller": caller,
                        "model_key": key,
                        "transport": method,
                        "success": False,
                        "duration_ms": int((time.time() - t0) * 1000),
                        "error": str(e)[:300],
                    }
                )
                _trace_log(f"[LLM Sync] ❌ 失败: {e} (transport={method}) ({trace})")
                continue

    return "❌ 系统繁忙，无法连接 AI。"
