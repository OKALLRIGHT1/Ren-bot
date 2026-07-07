from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional


GATEKEEPER_CAPABILITIES = {"info.weather_now", "info.weather_7d"}


@dataclass(frozen=True)
class CapabilityGateDecision:
    capability_id: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    approved: bool = False
    reason: str = ""


def should_use_gatekeeper(capability_id: str) -> bool:
    return str(capability_id or "").strip() in GATEKEEPER_CAPABILITIES


async def refine_capability_args(
    *,
    user_text: str,
    capability_id: str,
    initial_args: Optional[Dict[str, Any]],
    chat_with_ai: Callable[..., Any],
) -> Optional[CapabilityGateDecision]:
    capability_id = str(capability_id or "").strip()
    if not should_use_gatekeeper(capability_id):
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "你是工具调用看门人，只确认候选能力和抽取参数。"
                "只输出 JSON，不要解释。"
                "格式：{\"approved\":true,\"capability_id\":\"...\",\"args\":{},\"confidence\":0.0,\"reason\":\"...\"}。"
                "如果用户不是在请求这个能力，approved=false。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_text": str(user_text or ""),
                    "candidate_capability": capability_id,
                    "initial_args": dict(initial_args or {}),
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        raw = await _call_chat_with_ai(
            chat_with_ai,
            messages,
            task_type="tool_gatekeeper",
            caller="capability_gatekeeper",
        )
        data = _parse_json_object(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    returned_capability = str(data.get("capability_id") or "").strip()
    if returned_capability and returned_capability != capability_id:
        return None
    try:
        confidence = float(data.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    approved = bool(data.get("approved", True)) and confidence >= 0.6
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    return CapabilityGateDecision(
        capability_id=capability_id,
        args=dict(args or {}),
        confidence=confidence,
        approved=approved,
        reason=str(data.get("reason") or ""),
    )


def build_forced_capability_command(
    *,
    trigger: str,
    user_text: str,
    capability_id: str,
    capability_args: Optional[Dict[str, Any]],
) -> str:
    trigger = str(trigger or "").strip()
    capability_id = str(capability_id or "").strip()
    args = dict(capability_args or {})
    if trigger == "info_gateway" and capability_id in {"info.weather_now", "info.weather_7d"}:
        source = "weather_7d" if capability_id == "info.weather_7d" else "weather_now"
        params = _format_params(args)
        payload = source if not params else f"{source} {params}"
        return f"[CMD: {trigger} | {payload}]"
    return f"[CMD: {trigger} | {str(user_text or '').replace(']', ')')}]"


async def _call_chat_with_ai(chat_with_ai: Callable[..., Any], messages: list, **kwargs: Any) -> str:
    value = chat_with_ai(messages, **kwargs)
    if inspect.isawaitable(value):
        value = await value
    return str(value or "")


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else None


def _format_params(args: Dict[str, Any]) -> str:
    parts = []
    for key, value in args.items():
        key_text = str(key or "").strip()
        if not key_text or value in (None, ""):
            continue
        value_text = str(value).strip().replace('"', '\\"')
        if re.search(r"\s", value_text):
            value_text = f'"{value_text}"'
        parts.append(f"{key_text}={value_text}")
    return " ".join(parts)
