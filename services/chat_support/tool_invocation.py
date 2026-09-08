from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from modules.llm_types import ToolCallRequest


VALID_INVOCATION_MODES = {"cmd", "dual", "function"}

_TOOL_RULES_BLOCK_RE = re.compile(r"【工具调用规则】[\s\S]*?(?=\n【|\Z)")

_FUNCTION_TOOL_RULES = (
    "【工具调用规则】\n"
    "- 只有系统提供了 tools 时才可以调用工具。\n"
    "- 使用 function calling，不要在正文里写 [CMD:。\n"
    "- 工具调用不要带情绪标签。\n"
)

_DUAL_TOOL_RULES = (
    "【工具调用规则】\n"
    "- 只有系统提供了 tools 或本轮工具列表时才可以调用工具。\n"
    "- 优先使用 function calling，不要在正文夹带 [CMD:。\n"
    "- 工具调用不要带情绪标签。\n"
)


def normalize_invocation_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_INVOCATION_MODES:
        return raw
    return "cmd"


def rewrite_system_tool_rules(text: str, mode: str) -> str:
    mode = normalize_invocation_mode(mode)
    source = str(text or "")
    if mode == "cmd" or "【工具调用规则】" not in source:
        return source
    replacement = _FUNCTION_TOOL_RULES if mode == "function" else _DUAL_TOOL_RULES
    return _TOOL_RULES_BLOCK_RE.sub(replacement.rstrip() + "\n", source, count=1)


def should_attach_deferred_tool_catalog(*, need_tools: bool, deferred_tool_flow: bool) -> bool:
    return (not bool(need_tools)) and bool(deferred_tool_flow)


def should_rewrite_tool_rules(mode: str) -> bool:
    return normalize_invocation_mode(mode) in {"dual", "function"}


def should_attach_mcp_tool_prompt(triggers: Optional[Sequence[str]] = None) -> bool:
    return any(str(item or "").strip() in {"mcp_tools", "mcp"} for item in list(triggers or []))


def get_tool_invocation_mode() -> str:
    try:
        from modules.runtime_settings import load_runtime_settings

        raw = str(load_runtime_settings().get("tool_invocation_mode") or "").strip().lower()
        if raw in VALID_INVOCATION_MODES:
            return raw
    except Exception:
        pass
    try:
        from config import TOOL_INVOCATION_MODE

        raw = str(TOOL_INVOCATION_MODE or "dual").strip().lower()
        if raw in VALID_INVOCATION_MODES:
            return raw
    except Exception:
        pass
    return "dual"


def unpack_tool_call(call: Any) -> ToolCallRequest:
    if isinstance(call, ToolCallRequest):
        return call
    if isinstance(call, Mapping):
        raw_arguments = call.get("raw_arguments")
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            arguments, parsed_raw = parse_arguments_blob(arguments)
            if raw_arguments is None:
                raw_arguments = parsed_raw
        return ToolCallRequest(
            id=str(call.get("id") or "").strip() or "call_0",
            name=str(call.get("name") or "").strip(),
            arguments=dict(arguments or {}),
            raw_arguments=str(raw_arguments or ""),
        )
    name = str(getattr(call, "name", "") or "").strip()
    arguments = getattr(call, "arguments", None)
    if not isinstance(arguments, dict):
        arguments, raw_arguments = parse_arguments_blob(arguments)
    else:
        raw_arguments = str(getattr(call, "raw_arguments", "") or "")
    return ToolCallRequest(
        id=str(getattr(call, "id", "") or "").strip() or "call_0",
        name=name,
        arguments=dict(arguments or {}),
        raw_arguments=raw_arguments,
    )


def parse_arguments_blob(raw: Any) -> Tuple[Dict[str, Any], str]:
    if isinstance(raw, dict):
        return dict(raw), json.dumps(raw, ensure_ascii=False)
    text = str(raw or "").strip()
    if not text:
        return {}, ""
    try:
        data = json.loads(text)
    except Exception:
        return {"query": text}, text
    if isinstance(data, dict):
        return dict(data), text
    return {"query": text}, text


def schema_parameters(schema: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(schema, Mapping):
        return {}
    function = schema.get("function") if isinstance(schema.get("function"), Mapping) else schema
    params = function.get("parameters") if isinstance(function, Mapping) else None
    return dict(params) if isinstance(params, Mapping) else {}


def missing_required_arguments(
    plugin: Any,
    arguments: Optional[Mapping[str, Any]],
    schema: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    if schema is None:
        getter = getattr(plugin, "get_tool_schema", None)
        schema = getter() if callable(getter) else None
    params = schema_parameters(schema)
    required = [
        str(item).strip()
        for item in (params.get("required") or [])
        if str(item).strip()
    ]
    args = dict(arguments or {})
    missing = []
    for key in required:
        value = args.get(key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)
    return missing


def collect_invocation_args(
    plugin: Any,
    *,
    user_text: str = "",
    capability_id: str = "",
    initial_args: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    collector = getattr(plugin, "collect_invocation_args", None)
    if callable(collector):
        collected = collector(
            user_text,
            capability_id=capability_id,
            initial_args=dict(initial_args or {}),
        )
        if isinstance(collected, dict):
            return dict(collected)
    args = dict(initial_args or {})
    if user_text and not args:
        args["query"] = str(user_text)
    return args


def format_invocation(plugin: Any, arguments: Mapping[str, Any], raw_arguments: str = "") -> str:
    formatter = getattr(plugin, "format_invocation", None)
    if callable(formatter):
        formatted = formatter(dict(arguments or {}))
        if formatted is not None:
            return str(formatted).strip()
    args = dict(arguments or {})
    if not args:
        return str(raw_arguments or "").strip()
    if set(args.keys()) <= {"query", "text", "q"}:
        for key in ("query", "text", "q"):
            value = str(args.get(key) or "").strip()
            if value:
                return value
    parts = []
    for key, value in args.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        elif value is None:
            continue
        else:
            rendered = str(value).strip()
        if not rendered:
            continue
        parts.append(f"{key_text}={rendered}")
    return " ".join(parts) if parts else str(raw_arguments or "").strip()


def missing_required_message(name: str, missing: Sequence[str]) -> str:
    fields = ", ".join(str(item) for item in missing if str(item).strip())
    return (
        f"【{name} 未执行】缺少必填参数: {fields}。"
        "请向用户询问这些信息后再调用，不要编造。"
    )


def calls_from_iterable(calls: Optional[Iterable[Any]]) -> List[ToolCallRequest]:
    return [unpack_tool_call(item) for item in (calls or []) if item is not None]
