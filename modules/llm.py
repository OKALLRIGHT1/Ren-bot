# modules/llm.py
import inspect
import json
import re
import threading
import time
import uuid
from typing import AsyncGenerator, Optional
from urllib.parse import urlencode

import requests
from openai import AsyncOpenAI, OpenAI

from modules.llm_types import ChatCompletionResult, ToolCallRequest

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
_MODEL_COOLDOWN_LOCK = threading.RLock()
_MODEL_COOLDOWNS = {}
_DEFAULT_MODEL_COOLDOWN_SECONDS = 60.0
_MAX_MODEL_COOLDOWN_SECONDS = 900.0
_BLOCKED_MODEL_COOLDOWN_SECONDS = 600.0
_TIMEOUT_MODEL_COOLDOWN_SECONDS = 90.0
_RUNTIME_HEALTH = None
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _runtime_health():
    global _RUNTIME_HEALTH
    if _RUNTIME_HEALTH is None:
        from services.runtime_health import get_runtime_health

        _RUNTIME_HEALTH = get_runtime_health()
    return _RUNTIME_HEALTH


def _find_reset_seconds(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "reset_seconds":
                try:
                    return float(item)
                except (TypeError, ValueError):
                    return None
            found = _find_reset_seconds(item)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_reset_seconds(item)
            if found is not None:
                return found
    return None


def _rate_limit_delay(exc: BaseException):
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    body = getattr(exc, "body", None)
    lowered = str(exc or "").lower()
    markers = ("model_cooldown", "rate limit", "rate_limit")
    is_limited = (
        status == 429
        or "http 429" in lowered
        or any(marker in lowered for marker in markers)
    )
    if not is_limited and body is not None:
        is_limited = any(marker in str(body).lower() for marker in markers)
    if not is_limited:
        return None
    reset = _find_reset_seconds(body)
    if reset is None:
        reset = _DEFAULT_MODEL_COOLDOWN_SECONDS
    return min(_MAX_MODEL_COOLDOWN_SECONDS, max(1.0, float(reset)))


def _set_model_cooldown(model_key: str, *, until: float, reason: str) -> None:
    with _MODEL_COOLDOWN_LOCK:
        _MODEL_COOLDOWNS[model_key] = {
            "until": float(until),
            "reason": str(reason),
        }
    try:
        _runtime_health().report(
            f"model:{model_key}",
            "cooldown",
            "模型限流冷却中",
            details={
                "cooldown_until": float(until),
                "reason": str(reason),
            },
        )
    except Exception:
        pass


def _exception_text(exc: BaseException) -> str:
    parts = [str(exc or "")]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    return " ".join(parts).lower()


def _is_blocked_error(exc: BaseException) -> bool:
    text = _exception_text(exc)
    markers = (
        "request was blocked",
        "your request was blocked",
        "content_policy",
        "content policy",
        "blocked by",
    )
    if any(marker in text for marker in markers):
        return True
    if "blocked" in text and "rate" not in text:
        return True
    return False


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    text = _exception_text(exc)
    markers = (
        "timed out",
        "timeout",
        "read timeout",
        "connect timeout",
        "deadline exceeded",
    )
    return any(marker in text for marker in markers)


def _start_model_cooldown(
    model_key: str,
    exc: BaseException,
    *,
    now=None,
    all_transports_failed: bool = True,
):
    current = time.time() if now is None else float(now)
    delay = _rate_limit_delay(exc)
    reason = "rate_limit"
    if delay is None and _is_blocked_error(exc):
        if not all_transports_failed:
            return None
        delay = _BLOCKED_MODEL_COOLDOWN_SECONDS
        reason = "blocked"
    elif delay is None and all_transports_failed and _is_timeout_error(exc):
        delay = _TIMEOUT_MODEL_COOLDOWN_SECONDS
        reason = "timeout"
    if delay is None:
        return None
    _set_model_cooldown(
        model_key,
        until=current + delay,
        reason=reason,
    )
    return delay


def _clear_model_cooldown(model_key: str, *, summary: str) -> None:
    with _MODEL_COOLDOWN_LOCK:
        existed = _MODEL_COOLDOWNS.pop(model_key, None) is not None
    if not existed:
        return
    try:
        _runtime_health().report(
            f"model:{model_key}",
            "healthy",
            summary,
            details={},
        )
    except Exception:
        pass


def _model_cooldown_remaining(model_key: str, *, now=None) -> float:
    current = time.time() if now is None else float(now)
    with _MODEL_COOLDOWN_LOCK:
        record = _MODEL_COOLDOWNS.get(model_key)
        if not record:
            return 0.0
        remaining = float(record["until"]) - current
        if remaining > 0:
            return remaining
        _MODEL_COOLDOWNS.pop(model_key, None)
    try:
        _runtime_health().report(
            f"model:{model_key}",
            "healthy",
            "模型冷却已结束",
            details={},
        )
    except Exception:
        pass
    return 0.0


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
        model_base = str(resolved.get("base_url", "") or "").strip()
        model_key = str(resolved.get("api_key", "") or "").strip()
        provider_base = str(provider_cfg.get("base_url", "") or "").strip()
        provider_key = str(provider_cfg.get("api_key", "") or "").strip()
        # Model-level URL/key is an explicit override. Provider only fills blanks.
        if not model_base and provider_base:
            resolved["base_url"] = provider_base
        if not model_key and provider_key:
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


def _http_status_code(exc) -> Optional[int]:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    match = re.search(r"\b(4\d\d)\b", str(exc or ""))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _is_tools_client_error(exc) -> bool:
    status = _http_status_code(exc)
    if status in {401, 403, 408, 429}:
        return False
    if status is not None and 400 <= status < 500:
        return True
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "tool_choice",
            "tool_calls",
            "function calling",
            "unknown field",
            "unexpected keyword",
            "extra property",
            "does not support tool",
            "tools is not supported",
        )
    )


def _parse_tool_arguments(raw) -> tuple[dict, str]:
    if isinstance(raw, dict):
        return dict(raw), json.dumps(raw, ensure_ascii=False)
    text = str(raw or "").strip()
    if not text:
        return {}, ""
    try:
        data = json.loads(text)
    except Exception:
        return {}, text
    if isinstance(data, dict):
        return dict(data), text
    return {}, text


def _parse_message_tool_calls(message) -> tuple[ToolCallRequest, ...]:
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    if not raw_calls:
        return ()
    parsed = []
    for index, item in enumerate(raw_calls):
        if isinstance(item, dict):
            call_id = str(item.get("id") or "").strip() or f"call_{index}"
            function = item.get("function") or {}
            if not isinstance(function, dict):
                function = {}
            name = str(function.get("name") or "").strip()
            arguments, raw_arguments = _parse_tool_arguments(function.get("arguments"))
        else:
            call_id = str(getattr(item, "id", "") or "").strip() or f"call_{index}"
            function = getattr(item, "function", None)
            name = str(getattr(function, "name", "") or "").strip()
            arguments, raw_arguments = _parse_tool_arguments(
                getattr(function, "arguments", None)
            )
        if not name:
            continue
        parsed.append(
            ToolCallRequest(
                id=call_id,
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    return tuple(parsed)


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
    try:
        from modules.model_catalog import join_openai_compat_url

        return join_openai_compat_url(base_url, endpoint)
    except Exception:
        # Keep a local fallback so transport still works if catalog import fails.
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            raise ValueError("openai compatible call missing base_url")
        ep = str(endpoint or "").strip().lstrip("/")
        if not ep:
            raise ValueError("openai compatible call missing endpoint")
        # Treat any trailing /vN (v1/v4/...) as already versioned.
        import re

        if re.search(r"/v\d+$", base, re.IGNORECASE):
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


def _compat_request_headers(api_key: str = "") -> dict:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    key = str(api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _drop_sdk_fingerprint_headers(request) -> None:
    headers = getattr(request, "headers", None)
    if headers is None:
        return
    for key in list(headers.keys()):
        if str(key).lower().startswith("x-stainless"):
            try:
                del headers[key]
            except Exception:
                try:
                    headers.pop(key, None)
                except Exception:
                    pass


def _openai_default_headers() -> dict:
    return {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json",
    }


def _openai_sync_http_client(timeout_sec: float):
    import httpx

    return httpx.Client(
        timeout=float(timeout_sec),
        headers=_openai_default_headers(),
        event_hooks={"request": [_drop_sdk_fingerprint_headers]},
    )


def _openai_async_http_client(timeout_sec: float):
    import httpx

    async def _hook(request):
        _drop_sdk_fingerprint_headers(request)

    return httpx.AsyncClient(
        timeout=float(timeout_sec),
        headers=_openai_default_headers(),
        event_hooks={"request": [_hook]},
    )


def _build_openai_client(*, api_key, base_url, timeout_sec: float):
    kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "default_headers": _openai_default_headers(),
    }
    try:
        kwargs["http_client"] = _openai_sync_http_client(timeout_sec)
    except Exception:
        pass
    return OpenAI(**kwargs)


def _build_async_openai_client(*, api_key, base_url, timeout_sec: float):
    kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "default_headers": _openai_default_headers(),
    }
    try:
        kwargs["http_client"] = _openai_async_http_client(timeout_sec)
    except Exception:
        pass
    return AsyncOpenAI(**kwargs)


def _close_openai_client(client) -> None:
    closer = getattr(client, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:
        pass


async def _aclose_openai_client(client) -> None:
    closer = getattr(client, "close", None)
    if not callable(closer):
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass


def _optional_positive_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _chat_with_openai_responses(
    messages_context, config: dict, timeout: int = 30, max_tokens=None
) -> str:
    model_name = str((config or {}).get("model", "")).strip()
    api_key = str((config or {}).get("api_key", "")).strip()
    base_url = str((config or {}).get("base_url", "")).strip()
    if not model_name:
        raise ValueError("responses call missing model")
    if not base_url:
        raise ValueError("responses call missing base_url")

    url = _build_openai_compat_url(base_url, "responses")
    headers = _compat_request_headers(api_key)
    payload = {
        "model": model_name,
        "input": _messages_to_responses_input(messages_context),
    }
    token_budget = _optional_positive_int(max_tokens)
    if token_budget:
        payload["max_output_tokens"] = token_budget
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        fallback_payload = {
            "model": model_name,
            "input": _messages_to_text_block(messages_context),
        }
        if token_budget:
            fallback_payload["max_output_tokens"] = token_budget
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
    # Drop trailing OpenAI-style /vN before switching to Gemini /v1beta.
    import re

    base = re.sub(r"/v\d+$", "", base, flags=re.IGNORECASE)
    if not base:
        base = "https://generativelanguage.googleapis.com"
    url = f"{base}/v1beta/models/{model_name}:generateContent"
    headers = _compat_request_headers(api_key if api_key and not api_key.startswith("AIza") else "")
    if api_key and api_key.startswith("AIza"):
        url = f"{url}?{urlencode({'key': api_key})}"
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


def _chat_with_gemini_native(
    messages_context, config: dict, timeout: int = 30, max_tokens=None
) -> str:
    model_name = str((config or {}).get("model", "")).strip()
    api_key = str((config or {}).get("api_key", "")).strip()
    base_url = str((config or {}).get("base_url", "")).strip()
    if not model_name:
        raise ValueError("gemini native call missing model")
    url, headers = _build_gemini_native_url(base_url, model_name, api_key)
    generation_config = {"temperature": 0.7}
    token_budget = _optional_positive_int(max_tokens)
    if token_budget:
        generation_config["maxOutputTokens"] = token_budget
    payload = {
        "contents": _messages_to_gemini_contents(messages_context),
        "generationConfig": generation_config,
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

    client = None
    try:
        client = _build_async_openai_client(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout_sec=30,
        )
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
    finally:
        await _aclose_openai_client(client)

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
        remaining = _model_cooldown_remaining(key)
        if remaining > 0:
            _trace_log(
                f"[LLM Stream] 跳过冷却模型 model={key} remaining={remaining:.1f}s"
            )
            continue
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

        for method_idx, method in enumerate(attempts):
            try:
                if method == "openai":
                    client = _build_async_openai_client(
                        api_key=config["api_key"],
                        base_url=config["base_url"],
                        timeout_sec=20,
                    )
                    try:
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
                    finally:
                        await _aclose_openai_client(client)
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

                _clear_model_cooldown(key, summary="模型调用恢复")
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
                cooldown_delay = _start_model_cooldown(
                    key,
                    e,
                    all_transports_failed=method_idx >= len(attempts) - 1,
                )
                if yielded_any:
                    return
                if cooldown_delay is not None:
                    break
                continue

    yield "（所有模型连接失败，请检查网络或 Key）"


def _openai_chat_create(client, create_kwargs):
    return client.chat.completions.create(**create_kwargs)


def _result_from_openai_message(
    message,
    *,
    allow_empty_text: bool,
    transport: str,
):
    if isinstance(message, dict):
        content = _extract_text_content(message.get("content", ""))
    elif message is None:
        content = ""
    else:
        content = _extract_text_content(getattr(message, "content", ""))
    tool_calls = _parse_message_tool_calls(message)
    if not content and not (allow_empty_text and tool_calls):
        raise RuntimeError(f"empty content from transport={transport}")
    return content, tool_calls, ""


def chat_complete(
    messages_context,
    task_type="default",
    caller: str = "",
    request_id: str = "",
    timeout_sec: float = 30,
    model_keys_override=None,
    call_metadata=None,
    max_tokens=None,
    tools=None,
    tool_choice="auto",
) -> ChatCompletionResult:
    request_id = request_id or uuid.uuid4().hex[:8]
    caller = caller or "unknown"
    check = check_caller_task(caller, task_type)
    trace = (
        f"task={task_type} caller={caller} req={request_id} tid={threading.get_ident()}"
    )
    tools_payload = list(tools) if tools else None
    fc_skipped_reason = ""

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

    busy = ChatCompletionResult(text="❌ 系统繁忙，无法连接 AI。")

    for key_idx, key in enumerate(model_keys, 1):
        remaining = _model_cooldown_remaining(key)
        if remaining > 0:
            _trace_log(
                f"[LLM Sync] 跳过冷却模型 model={key} remaining={remaining:.1f}s ({trace})"
            )
            continue
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

        for method_idx, method in enumerate(attempts):
            use_tools = bool(tools_payload) and method == "openai"
            attempt_skipped = ""
            if tools_payload and method != "openai":
                attempt_skipped = "unsupported_transport"
            try:
                tool_calls = ()
                if method == "openai":
                    client = _build_openai_client(
                        api_key=config["api_key"],
                        base_url=config["base_url"],
                        timeout_sec=float(timeout_sec),
                    )
                    try:
                        create_kwargs = {
                            "model": config["model"],
                            "messages": messages_context,
                            "timeout": float(timeout_sec),
                        }
                        token_budget = _optional_positive_int(max_tokens)
                        if token_budget:
                            create_kwargs["max_tokens"] = token_budget
                        if use_tools:
                            create_kwargs["tools"] = tools_payload
                            if tool_choice:
                                create_kwargs["tool_choice"] = tool_choice
                        try:
                            response = _openai_chat_create(client, create_kwargs)
                        except Exception as tools_exc:
                            if use_tools and _is_tools_client_error(tools_exc):
                                fallback_kwargs = dict(create_kwargs)
                                fallback_kwargs.pop("tools", None)
                                fallback_kwargs.pop("tool_choice", None)
                                _trace_log(
                                    f"[LLM Sync] tools unsupported, retry without tools ({trace})"
                                )
                                response = _openai_chat_create(client, fallback_kwargs)
                                attempt_skipped = "tools_unsupported"
                                use_tools = False
                            else:
                                raise
                    finally:
                        _close_openai_client(client)
                    message = response.choices[0].message
                    content, tool_calls, _finish = _result_from_openai_message(
                        message,
                        allow_empty_text=bool(tools_payload) and not attempt_skipped,
                        transport=method,
                    )
                    finish_reason = str(
                        getattr(response.choices[0], "finish_reason", "") or ""
                    )
                elif method == "openai_responses":
                    content = _chat_with_openai_responses(
                        messages_context,
                        config,
                        timeout=float(timeout_sec),
                        max_tokens=max_tokens,
                    )
                    finish_reason = ""
                    if not content:
                        raise RuntimeError(f"empty content from transport={method}")
                elif method == "gemini_native":
                    content = _chat_with_gemini_native(
                        messages_context,
                        config,
                        timeout=float(timeout_sec),
                        max_tokens=max_tokens,
                    )
                    finish_reason = ""
                    if not content:
                        raise RuntimeError(f"empty content from transport={method}")
                else:
                    raise RuntimeError(f"unsupported transport: {method}")

                if attempt_skipped:
                    fc_skipped_reason = attempt_skipped
                elif tools_payload and not use_tools:
                    fc_skipped_reason = fc_skipped_reason or "unsupported_transport"

                _clear_model_cooldown(key, summary="模型调用恢复")
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
                            "fc_skipped_reason": str(fc_skipped_reason or ""),
                        }
                    )
                return ChatCompletionResult(
                    text=str(content or ""),
                    tool_calls=tool_calls,
                    model_key=str(key),
                    transport=str(method),
                    finish_reason=str(finish_reason or ""),
                    fc_skipped_reason=str(fc_skipped_reason or ""),
                )
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
                cooldown_delay = _start_model_cooldown(
                    key,
                    e,
                    all_transports_failed=method_idx >= len(attempts) - 1,
                )
                if cooldown_delay is not None:
                    break
                continue

    return busy


def chat_with_ai(
    messages_context,
    task_type="default",
    caller: str = "",
    request_id: str = "",
    timeout_sec: float = 30,
    model_keys_override=None,
    call_metadata=None,
    max_tokens=None,
):
    result = chat_complete(
        messages_context,
        task_type=task_type,
        caller=caller,
        request_id=request_id,
        timeout_sec=timeout_sec,
        model_keys_override=model_keys_override,
        call_metadata=call_metadata,
        max_tokens=max_tokens,
    )
    return result.text
