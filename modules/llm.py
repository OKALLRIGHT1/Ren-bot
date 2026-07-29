# modules/llm.py
import threading
import time
import uuid
from typing import AsyncGenerator
from urllib.parse import urlencode

import requests
from openai import AsyncOpenAI, OpenAI

from config import (
    LLM_ROUTER,
    LLM_ROUTER_STRICT_ORDER,
    MODELS,
    PROVIDERS,
    SENSOR_VISION_MODEL,
)
from modules.security_redaction import redact_sensitive_text
from modules.task_registry import check_caller_task

try:
    from modules.model_transport_state import (
        get_preferred_model,
        get_preferred_transport,
        record_failure,
        record_success,
        record_task_model_success,
    )
except Exception:
    # Transport memory is best-effort and must not block LLM calls.
    def get_preferred_model(task_key: str):
        return None

    def get_preferred_transport(model_key: str):
        return None

    def record_success(model_key: str, transport: str):
        return False

    def record_task_model_success(task_key: str, model_key: str):
        return False

    def record_failure(model_key: str, transport: str, error: str = ""):
        return False


_LOG_LOCK = threading.Lock()
_METRIC_LOCK = threading.Lock()
_METRICS = []
_MAX_METRICS = 300
_UNKNOWN_CALLER_WARNED = set()


def _trace_log(*lines):
    with _LOG_LOCK:
        for line in lines:
            print(line)


def _record_metric(entry: dict):
    with _METRIC_LOCK:
        _METRICS.append(entry)
        if len(_METRICS) > _MAX_METRICS:
            del _METRICS[: len(_METRICS) - _MAX_METRICS]


def _warn_caller_task_check(check, *, prefix: str, trace: str = "") -> None:
    if check.known:
        if not check.ok:
            suffix = f" ({trace})" if trace else ""
            _trace_log(
                f"{prefix} caller/task_type mismatch caller={check.caller} expected={check.expected_task_type} actual={check.task_type}{suffix}"
            )
        return
    key = (check.caller, check.task_type)
    if key in _UNKNOWN_CALLER_WARNED:
        return
    _UNKNOWN_CALLER_WARNED.add(key)
    suffix = f" ({trace})" if trace else ""
    _trace_log(
        f"{prefix} unknown caller caller={check.caller} task={check.task_type}{suffix}"
    )


def get_recent_llm_metrics(limit: int = 50):
    with _METRIC_LOCK:
        n = max(1, int(limit))
        return list(_METRICS[-n:])


def _model_style(config: dict) -> str:
    return str((config or {}).get("api_style", "")).strip().lower()


def _resolve_model_config(config: dict) -> dict:
    resolved = dict(config or {})
    provider_name = str(resolved.get("provider") or "").strip()
    provider_cfg = PROVIDERS.get(provider_name) if provider_name else None
    if isinstance(provider_cfg, dict):
        provider_base = str(provider_cfg.get("base_url", "") or "").strip()
        provider_key = str(provider_cfg.get("api_key", "") or "").strip()
        if provider_base:
            resolved["base_url"] = provider_base
        if provider_key:
            resolved["api_key"] = provider_key
    return resolved


def _is_gemini_model(config: dict) -> bool:
    model_name = str((config or {}).get("model", "")).lower()
    return "gemini" in model_name


def _is_glm_model(config: dict) -> bool:
    model_name = str((config or {}).get("model", "")).lower()
    base_url = str((config or {}).get("base_url", "")).lower()
    return "glm" in model_name or "bigmodel.cn" in base_url


def _prefers_openai_only(config: dict) -> bool:
    base_url = str((config or {}).get("base_url", "")).lower()
    style = _model_style(config)
    if style in {"openai"}:
        return True
    return any(
        host in base_url
        for host in [
            "open.bigmodel.cn",
            "openrouter.ai",
            "integrate.api.nvidia.com",
            "api.deepseek.com",
            "x666.me",
            "api.nih.cc",
            "jiuuij.de5.net",
            "ai.qaq.al",
            "localhost:8317",
        ]
    )


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
    for msg in messages_context or []:
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
    for msg in messages_context or []:
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


def _chat_with_openai_responses(
    messages_context, config: dict, timeout: int = 30
) -> str:
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
        fallback_resp = requests.post(
            url, headers=headers, json=fallback_payload, timeout=timeout
        )
        if fallback_resp.status_code >= 400:
            raise RuntimeError(
                f"openai_responses HTTP {fallback_resp.status_code}: {fallback_resp.text[:280]}"
            )
        resp = fallback_resp
    data = resp.json()
    text = _extract_responses_text(data)
    if text:
        return text
    raise RuntimeError("openai_responses returned empty content")


def _build_gemini_native_url(
    base_url: str, model_name: str, api_key: str
) -> tuple[str, dict]:
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
    for msg in messages_context or []:
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
            first["parts"] = [
                {"text": f"[System Instruction]\n{sys_text}\n\n{first_text}"}
            ]
        else:
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": f"[System Instruction]\n{sys_text}"}],
                }
            )

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
    is_glm = _is_glm_model(config)

    if is_glm:
        attempts = ["openai"]
        preferred = get_preferred_transport(model_key) if model_key else None
        if preferred == "openai":
            return attempts
        return attempts

    if _prefers_openai_only(config):
        attempts = ["openai"]
        preferred = get_preferred_transport(model_key) if model_key else None
        if preferred == "openai":
            return attempts
        return attempts

    if style in {"responses", "openai_responses"}:
        base = (
            ["openai_responses", "openai", "gemini_native"]
            if is_gemini
            else ["openai_responses", "openai"]
        )
    elif style in {"gemini_native", "google"}:
        base = ["gemini_native", "openai", "openai_responses"]
    elif style in {"openai", "gemini"}:
        base = (
            ["openai", "openai_responses", "gemini_native"]
            if is_gemini
            else ["openai", "openai_responses"]
        )
    else:
        base = (
            ["openai", "openai_responses", "gemini_native"]
            if is_gemini
            else ["openai", "openai_responses"]
        )

    attempts = _dedupe(base)
    preferred = get_preferred_transport(model_key) if model_key else None
    if preferred and preferred in attempts:
        attempts = [preferred] + [x for x in attempts if x != preferred]
    return attempts


async def analyze_image(
    image_base64: str,
    prompt: str = "请详细描述这张图片的内容。",
    model_name: str = None,
    caller: str = "",
) -> str:
    caller = caller or "vision"
    check = check_caller_task(caller, "vision")
    _warn_caller_task_check(check, prefix="[Vision]")
    target_key = model_name if model_name else (SENSOR_VISION_MODEL or "default")
    config = MODELS.get(target_key)
    if not config:
        return f"（视觉配置错误：找不到模型 {target_key}）"
    config = _resolve_model_config(config)

    print(f"[Vision] 调用模型: {target_key} caller={caller}")

    try:
        client = AsyncOpenAI(api_key=config["api_key"], base_url=config["base_url"])
        response = await client.chat.completions.create(
            model=config["model"],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
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


async def chat_with_ai_stream(
    messages_context, task_type="default", caller: str = ""
) -> AsyncGenerator[str, None]:
    caller = caller or "unknown"
    check = check_caller_task(caller, task_type)
    _warn_caller_task_check(check, prefix="[LLM Stream]")
    model_keys = LLM_ROUTER.get(task_type, LLM_ROUTER.get("default", []))
    if isinstance(model_keys, str):
        model_keys = [model_keys]
    if not model_keys:
        yield "（配置错误：无可用模型）"
        return

    preferred_model = None if LLM_ROUTER_STRICT_ORDER else get_preferred_model(task_type)
    if preferred_model and preferred_model in model_keys:
        model_keys = [preferred_model] + [m for m in model_keys if m != preferred_model]

    for idx, key in enumerate(model_keys, 1):
        config = MODELS.get(key)
        if not config:
            continue
        config = _resolve_model_config(config)

        print(f"[LLM Stream] 尝试 {idx}/{len(model_keys)}: {key}")
        yielded_any = False
        t0 = time.time()
        attempts = _build_attempt_order(config, key)
        preferred = get_preferred_transport(key)
        print(
            f"[LLM Stream] 传输顺序 model={key}: {attempts} preferred={preferred or '-'}"
        )

        for method in attempts:
            try:
                if method == "openai":
                    client = AsyncOpenAI(
                        api_key=config["api_key"], base_url=config["base_url"]
                    )
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
                    text = _chat_with_openai_responses(
                        messages_context, config, timeout=20
                    )
                    if text:
                        yielded_any = True
                        yield text
                elif method == "gemini_native":
                    text = _chat_with_gemini_native(
                        messages_context, config, timeout=20
                    )
                    if text:
                        yielded_any = True
                        yield text
                else:
                    raise RuntimeError(f"unsupported transport: {method}")

                record_success(key, method)
                record_task_model_success(task_type, key)
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
                safe_error = redact_sensitive_text(e)
                record_failure(key, method, safe_error)
                print(f"[LLM Stream] 失败: {safe_error} (model={key}, transport={method})")
                _record_metric(
                    {
                        "ts": time.time(),
                        "mode": "stream",
                        "task_type": task_type,
                        "model_key": key,
                        "transport": method,
                        "success": False,
                        "duration_ms": int((time.time() - t0) * 1000),
                        "error": safe_error[:300],
                    }
                )
                if yielded_any:
                    return
                continue

    yield "（所有模型连接失败，请检查网络或 Key）"


def chat_with_ai(
    messages_context,
    task_type="default",
    caller: str = "",
    request_id: str = "",
    timeout_sec: float = 30,
    model_keys_override=None,
    call_metadata=None,
):
    request_id = request_id or uuid.uuid4().hex[:8]
    caller = caller or "unknown"
    check = check_caller_task(caller, task_type)
    trace = (
        f"task={task_type} caller={caller} req={request_id} tid={threading.get_ident()}"
    )

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
    _warn_caller_task_check(check, prefix="[LLM Sync]", trace=trace)

    model_keys = (
        model_keys_override
        if model_keys_override is not None
        else LLM_ROUTER.get(task_type, LLM_ROUTER.get("default", []))
    )
    if isinstance(model_keys, str):
        model_keys = [model_keys]
    else:
        model_keys = [str(key).strip() for key in (model_keys or []) if str(key).strip()]

    preferred_model = (
        None
        if model_keys_override is not None or LLM_ROUTER_STRICT_ORDER
        else get_preferred_model(task_type)
    )
    if preferred_model and preferred_model in model_keys:
        model_keys = [preferred_model] + [m for m in model_keys if m != preferred_model]

    for key_idx, key in enumerate(model_keys, 1):
        _trace_log(f"[LLM Sync] 尝试 #{key_idx}: {key} ({trace})")
        config = MODELS.get(key)
        if not config:
            continue
        config = _resolve_model_config(config)

        t0 = time.time()
        attempts = _build_attempt_order(config, key)
        preferred = get_preferred_transport(key)
        _trace_log(
            f"[LLM Sync] transport_order={attempts} preferred={preferred or '-'} ({trace})"
        )

        for method in attempts:
            try:
                if method == "openai":
                    client = OpenAI(
                        api_key=config["api_key"], base_url=config["base_url"]
                    )
                    response = client.chat.completions.create(
                        model=config["model"],
                        messages=messages_context,
                        timeout=float(timeout_sec),
                    )
                    raw_content = getattr(response.choices[0].message, "content", "")
                    content = _extract_text_content(raw_content)
                elif method == "openai_responses":
                    content = _chat_with_openai_responses(
                        messages_context, config, timeout=float(timeout_sec)
                    )
                elif method == "gemini_native":
                    content = _chat_with_gemini_native(
                        messages_context, config, timeout=float(timeout_sec)
                    )
                else:
                    raise RuntimeError(f"unsupported transport: {method}")

                if not content:
                    raise RuntimeError(f"empty content from transport={method}")

                record_success(key, method)
                record_task_model_success(task_type, key)
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
                _trace_log(
                    f"[LLM Sync] ✅ 成功({method}) (len={len(content)}) ({trace})"
                )
                if isinstance(call_metadata, dict):
                    call_metadata.update(
                        {
                            "model_key": str(key),
                            "transport": str(method),
                        }
                    )
                return str(content)
            except Exception as e:
                safe_error = redact_sensitive_text(e)
                record_failure(key, method, safe_error)
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
                        "error": safe_error[:300],
                    }
                )
                _trace_log(f"[LLM Sync] ❌ 失败: {safe_error} (transport={method}) ({trace})")
                continue

    return "❌ 系统繁忙，无法连接 AI。"
