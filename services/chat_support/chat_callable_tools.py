"""Tools a plain chat turn may call without a keyword route.

A plugin is on this list only when its config says self_callable and it has
a function schema. Search still runs through the delegate search path.
Anything else the model names is ignored. Task-shaped sentences can also
see the short executable catalog and tool_search; the next step receives
the schemas those hits can actually call.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Optional

from modules.llm_types import ChatCompletionResult, ToolCallRequest
from services.chat_support.search_flow_service import run_search_delegate_query
from services.chat_support.tool_flow_service import (
    ReactFirstPassResult,
    _assistant_tool_call_message,
    _call_chat_complete,
    _tool_result_message,
)
from services.chat_support.tool_invocation import normalize_invocation_mode
from services.chat_support.tool_schema import (
    TOOL_SEARCH_SCHEMA,
    build_openai_tools,
    iter_plugin_tool_schemas,
    merge_openai_tools,
    openai_tool_names,
    plugin_is_self_callable,
    plugin_trigger_name,
    resolve_plugin,
)

CHAT_SEARCH_NAMES = {"search", "search_web"}
_CHAT_DISCOVERY_TYPES = {"react"}

_CMD_RE = re.compile(
    r"\[CMD:\s*([A-Za-z0-9_\-]+)\s*(?:[\|／/]\s*|\s+)(.*?)\]",
    flags=re.IGNORECASE | re.DOTALL,
)

_CHAT_SEARCH_HINT = (
    "【闲聊里可以自己查】\n"
    "你会的、刚聊过的、情绪和看法，直接回答，不要调用工具。\n"
    "遇到你不确定的事实、新闻、价格、人物或需要出处的问题，调用 search，再用结果回答。\n"
    "问候、撒娇、角色扮演、私事不要搜。不要用 search 查天气。\n"
)

_CHAT_SEARCH_HINT_CMD = (
    "【闲聊里可以自己查】\n"
    "你会的、刚聊过的、情绪和看法，直接回答。\n"
    "遇到你不确定的事实、新闻、价格、人物或需要出处的问题，只输出这一行："
    "[CMD: search | 搜索词]\n"
    "问候、撒娇、角色扮演、私事不要搜。不要查天气。\n"
)


def chat_search_hint(invocation_mode: str) -> str:
    if normalize_invocation_mode(invocation_mode) == "cmd":
        return _CHAT_SEARCH_HINT_CMD
    return _CHAT_SEARCH_HINT


def _plugin_names(plugin: Any, key: str = "") -> set[str]:
    names = {
        str(key or "").strip(),
        plugin_trigger_name(plugin),
        str(getattr(plugin, "trigger", "") or "").strip(),
        str(getattr(plugin, "llm_command", "") or "").strip(),
    }
    for extra in getattr(plugin, "llm_commands", None) or []:
        text = str(extra or "").strip()
        if text:
            names.add(text)
    fallback = str(getattr(plugin, "llm_command", "") or plugin_trigger_name(plugin) or key)
    for schema in iter_plugin_tool_schemas(plugin, fallback):
        schema_name = str(schema.get("name") or "").strip()
        if schema_name:
            names.add(schema_name)
    return {item for item in names if item}


def _is_search_plugin(plugin: Any, key: str = "") -> bool:
    lowered = {item.lower() for item in _plugin_names(plugin, key)}
    return bool(lowered & CHAT_SEARCH_NAMES)


def iter_self_callable_plugins(plugin_manager: Any) -> list[Any]:
    found: list[Any] = []
    seen: set[int] = set()
    for mapping_name in ("plugins", "react_map", "delegate_map", "direct_map"):
        mapping = getattr(plugin_manager, mapping_name, None) or {}
        if not isinstance(mapping, dict):
            continue
        for key, plugin in mapping.items():
            if plugin is None or id(plugin) in seen:
                continue
            if not plugin_is_self_callable(plugin):
                continue
            names = _plugin_names(plugin, str(key or ""))
            fallback = next(iter(names), "")
            if not iter_plugin_tool_schemas(plugin, fallback):
                continue
            seen.add(id(plugin))
            found.append(plugin)
    return found


def chat_self_callable_available(plugin_manager: Any) -> bool:
    return bool(iter_self_callable_plugins(plugin_manager))


def find_chat_search_plugin(plugin_manager: Any) -> Any:
    for plugin in iter_self_callable_plugins(plugin_manager):
        if _is_search_plugin(plugin):
            return plugin
    return None


def chat_search_is_available(plugin_manager: Any) -> bool:
    return find_chat_search_plugin(plugin_manager) is not None


def _plugin_lookup_keys(plugin: Any) -> list[str]:
    keys: list[str] = []
    for value in (
        plugin_trigger_name(plugin),
        getattr(plugin, "llm_command", ""),
        getattr(plugin, "trigger", ""),
    ):
        text = str(value or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def chat_self_callable_openai_tools(plugin_manager: Any, invocation_mode: str) -> list:
    if normalize_invocation_mode(invocation_mode) == "cmd":
        return []
    triggers: list[str] = []
    for plugin in iter_self_callable_plugins(plugin_manager):
        triggers.extend(_plugin_lookup_keys(plugin))
    if not triggers:
        return []
    return build_openai_tools(
        plugin_manager,
        triggers,
        allowed_types={"delegate", "react", "direct"},
        include_tool_search=False,
    )


def chat_search_openai_tools(plugin_manager: Any, invocation_mode: str) -> list:
    if not chat_search_is_available(plugin_manager):
        return []
    if normalize_invocation_mode(invocation_mode) == "cmd":
        return []
    return build_openai_tools(
        plugin_manager,
        ["search_web", "search"],
        allowed_types={"delegate", "react"},
        include_tool_search=False,
    )


def _lookup_name_set(plugins: list[Any]) -> set[str]:
    names: set[str] = set()
    for plugin in plugins:
        for item in _plugin_names(plugin):
            names.add(item)
            names.add(item.lower())
    return names


def _is_search_call_name(name: str, plugins: list[Any]) -> bool:
    key = str(name or "").strip()
    if key.lower() not in CHAT_SEARCH_NAMES:
        return False
    return any(_is_search_plugin(plugin) for plugin in plugins)


def search_query_from_completion(completion: ChatCompletionResult) -> str:
    for call in completion.tool_calls or ():
        name = str(getattr(call, "name", "") or "").strip().lower()
        if name not in CHAT_SEARCH_NAMES:
            continue
        arguments = getattr(call, "arguments", None) or {}
        query = str(arguments.get("query") or "").strip() if isinstance(arguments, dict) else ""
        if query:
            return query
    for command, args in _CMD_RE.findall(str(completion.text or "")):
        if str(command or "").strip().lower() not in CHAT_SEARCH_NAMES:
            continue
        query = re.sub(r"\s+", " ", str(args or "")).strip()
        if query:
            return query
    return ""


def _short_desc(plugin: Any) -> str:
    from modules.plugin_manager import plugin_public_description

    desc = plugin_public_description(plugin).replace("\n", " ").strip()
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) > 42:
        desc = desc[:42] + "…"
    return desc


def _chat_hint(plugins: list[Any], invocation_mode: str, catalog: str) -> str:
    search_plugins = [plugin for plugin in plugins if _is_search_plugin(plugin)]
    others = [plugin for plugin in plugins if plugin not in search_plugins]
    parts: list[str] = []
    if search_plugins and not others:
        parts.append(chat_search_hint(invocation_mode).strip())
    elif plugins:
        lines = [
            "【闲聊里可以自己调用】",
            "你会的、刚聊过的、情绪和看法，直接回答，不要调用工具。",
            "只有下面这些可以自己调用。需要时调用，再用结果回答。",
        ]
        mode = normalize_invocation_mode(invocation_mode)
        for plugin in plugins:
            command = str(getattr(plugin, "llm_command", "") or plugin_trigger_name(plugin)).strip()
            desc = _short_desc(plugin)
            if mode == "cmd":
                example = str(getattr(plugin, "example_arg", "") or "参数").strip()
                lines.append(f"- [CMD: {command} | {example}] {desc}".rstrip())
            else:
                schemas = iter_plugin_tool_schemas(plugin, command)
                shown = str(schemas[0].get("name") or command) if schemas else command
                lines.append(f"- {shown}: {desc}".rstrip())
        if search_plugins:
            lines.append("不确定的事实、新闻、价格、人物可以自己查，调用 search。不要用 search 查天气。")
        lines.append("问候、撒娇、角色扮演、私事不要调用。")
        parts.append("\n".join(lines))
    if catalog.strip():
        parts.append(catalog.strip())
    return "\n\n".join(part for part in parts if part)


def _allowed_command(name: str, allowed: set[str]) -> bool:
    key = str(name or "").strip()
    if not key:
        return False
    return key in allowed or key.lower() in allowed


def _parse_chat_actions(
    completion: ChatCompletionResult,
    *,
    self_names: set[str],
    admitted: set[str],
    plugins: list[Any],
) -> dict[str, Any]:
    allowed = set(self_names) | set(admitted)
    search_query = ""
    self_calls: list[ToolCallRequest] = []
    admitted_calls: list[ToolCallRequest] = []
    tool_search_queries: list[str] = []
    cmd_lines: list[tuple[str, str]] = []
    fc_used = False
    cmd_used = False

    for call in completion.tool_calls or ():
        name = str(getattr(call, "name", "") or "").strip()
        lowered = name.lower()
        if lowered == "tool_search":
            arguments = getattr(call, "arguments", None) or {}
            query = str(arguments.get("query") or "").strip() if isinstance(arguments, dict) else ""
            if query:
                tool_search_queries.append(query)
            fc_used = True
            continue
        if not _allowed_command(name, allowed):
            continue
        fc_used = True
        if _is_search_call_name(name, plugins) and _allowed_command(name, self_names):
            arguments = getattr(call, "arguments", None) or {}
            query = str(arguments.get("query") or "").strip() if isinstance(arguments, dict) else ""
            if query and not search_query:
                search_query = query
            continue
        if _allowed_command(name, self_names):
            self_calls.append(call)
        else:
            admitted_calls.append(call)

    for command, args in _CMD_RE.findall(str(completion.text or "")):
        name = str(command or "").strip()
        query = re.sub(r"\s+", " ", str(args or "")).strip()
        lowered = name.lower()
        if lowered == "tool_search":
            if query:
                tool_search_queries.append(query)
            cmd_used = True
            continue
        if not _allowed_command(name, allowed):
            continue
        cmd_used = True
        if _is_search_call_name(name, plugins) and _allowed_command(name, self_names):
            if query and not search_query:
                search_query = query
            continue
        if query or name:
            cmd_lines.append((name, query))
    return {
        "search_query": search_query,
        "self_calls": self_calls,
        "admitted_calls": admitted_calls,
        "tool_search_queries": tool_search_queries,
        "cmd_lines": cmd_lines,
        "fc_used": fc_used,
        "cmd_used": cmd_used,
    }


async def _record(callback: Optional[Callable[..., Any]], **event: Any) -> None:
    if callback is None:
        return
    result = callback(**event)
    if inspect.isawaitable(result):
        await result


def _plugin_for_command(plugin_manager: Any, name: str) -> Any:
    plugin = resolve_plugin(plugin_manager, name)
    if plugin is not None:
        return plugin
    key = str(name or "").strip().lower()
    for candidate in iter_self_callable_plugins(plugin_manager):
        if key in {item.lower() for item in _plugin_names(candidate)}:
            return candidate
    return None


async def _execute_named_calls(
    *,
    plugin_manager: Any,
    calls: list[ToolCallRequest],
    cmd_lines: list[tuple[str, str]],
    ctx: dict,
    invocation_mode: str,
) -> tuple[list[str], list[str]]:
    outputs: list[str] = []
    used: list[str] = []
    grouped: dict[str, list[ToolCallRequest]] = {}
    for call in calls:
        plugin = _plugin_for_command(plugin_manager, call.name)
        if plugin is None:
            continue
        grouped.setdefault(str(getattr(plugin, "type", "react") or "react"), []).append(call)
    executor = getattr(plugin_manager, "execute_invocations", None)
    if callable(executor):
        for plugin_type, group in grouped.items():
            triggered, _clean, rows, triggers = await executor(
                group,
                dict(ctx or {}),
                allow_tools=True,
                allowed_types={plugin_type},
                invocation_mode=invocation_mode,
            )
            if triggered or rows:
                outputs.extend(str(item) for item in (rows or []) if str(item).strip())
                used.extend(str(item) for item in (triggers or []) if str(item).strip())
    if not cmd_lines:
        return outputs, used
    command_executor = getattr(plugin_manager, "execute_commands", None)
    if not callable(command_executor):
        return outputs, used
    by_type: dict[str, list[str]] = {}
    for name, args in cmd_lines:
        plugin = _plugin_for_command(plugin_manager, name)
        plugin_type = str(getattr(plugin, "type", "react") or "react") if plugin is not None else "react"
        by_type.setdefault(plugin_type, []).append(f"[CMD: {name} | {args}]")
    for plugin_type, lines in by_type.items():
        triggered, _clean, rows, triggers = await command_executor(
            "\n".join(lines),
            dict(ctx or {}),
            allow_tools=True,
            allowed_types={plugin_type},
        )
        if triggered or rows:
            outputs.extend(str(item) for item in (rows or []) if str(item).strip())
            used.extend(str(item) for item in (triggers or []) if str(item).strip())
    return outputs, used


def _finish_with_tools(
    *,
    outputs: list[str],
    used: list[str],
    messages: list,
    invocation_mode: str,
    fc_used: bool,
    cmd_used: bool,
    fc_skipped_reason: str,
    sent_tool_names: list[str],
    offered_triggers: list[str],
) -> ReactFirstPassResult:
    if not outputs:
        outputs = ["没有拿到可用结果。"]
    return ReactFirstPassResult(
        reply="",
        triggered=True,
        clean_thought="",
        tool_results=list(outputs),
        used_triggers=list(used),
        context_messages=messages,
        invocation_mode=str(invocation_mode or "cmd"),
        fc_used=bool(fc_used),
        cmd_used=bool(cmd_used),
        fc_skipped_reason=str(fc_skipped_reason or ""),
        sent_tool_names=list(sent_tool_names),
        offered_triggers=list(offered_triggers),
    )


async def run_chat_model_pass(
    *,
    context_messages: list,
    ctx: dict,
    task_default: str,
    plugin_manager: Any,
    chat_with_ai: Callable[..., Any],
    invocation_mode: str = "cmd",
    request_context: Any = None,
    record_tool_execution: Optional[Callable[..., Any]] = None,
    discover_tools: bool = False,
) -> ReactFirstPassResult:
    """One character reply. Self-callable tools are real schemas.

    When discover_tools is set, a tool_search hit adds those schemas on the
    next request. Calls the model did not get admitted are not executed.
    """
    plugins = iter_self_callable_plugins(plugin_manager)
    mode = normalize_invocation_mode(invocation_mode)
    catalog = ""
    if discover_tools:
        getter = getattr(plugin_manager, "get_deferred_tool_prompt", None)
        if callable(getter):
            try:
                catalog = str(
                    getter(invocation_mode=mode, allowed_types=set(_CHAT_DISCOVERY_TYPES))
                    or ""
                )
            except TypeError:
                catalog = str(getter(invocation_mode=mode) or "")
    messages = list(context_messages or [])
    hint = _chat_hint(plugins, mode, catalog)
    if hint:
        messages.append({"role": "system", "content": hint})
    tools = chat_self_callable_openai_tools(plugin_manager, mode)
    if discover_tools and mode != "cmd":
        tools = merge_openai_tools(
            tools,
            [{"type": "function", "function": dict(TOOL_SEARCH_SCHEMA)}],
        )
    self_names = _lookup_name_set(plugins)
    admitted: set[str] = set()
    sent_tool_names: list[str] = []
    offered = []
    for plugin in plugins:
        for key in _plugin_lookup_keys(plugin):
            if key not in offered:
                offered.append(key)
    fc_skipped_reason = ""
    fc_used = False
    cmd_used = False

    for step in range(2):
        for name in openai_tool_names(tools):
            if name not in sent_tool_names:
                sent_tool_names.append(name)
        completion = await _call_chat_complete(
            chat_with_ai,
            messages,
            task_type=task_default,
            caller="chat_default_reply" if step == 0 else "chat_default_reply_2",
            tools=tools or None,
            request_context=request_context,
        )
        if completion.fc_skipped_reason:
            fc_skipped_reason = str(completion.fc_skipped_reason or "")
        parsed = _parse_chat_actions(
            completion,
            self_names=self_names,
            admitted=admitted,
            plugins=plugins,
        )
        fc_used = fc_used or bool(parsed["fc_used"])
        cmd_used = cmd_used or bool(parsed["cmd_used"])
        outputs: list[str] = []
        used: list[str] = []
        search_query = str(parsed["search_query"] or "")
        if search_query:
            triggered, _clean, results, search_used = await run_search_delegate_query(
                query=search_query,
                ctx=dict(ctx or {}),
                plugin_manager=plugin_manager,
            )
            if triggered or results:
                outputs.extend(str(item) for item in (results or []) if str(item).strip())
                used.extend(str(item) for item in (search_used or []) if str(item).strip())
                if not used:
                    used.append("search_web")
                if not outputs:
                    outputs.append("没有搜到可用结果。")
        extra_outputs, extra_used = await _execute_named_calls(
            plugin_manager=plugin_manager,
            calls=list(parsed["self_calls"]) + list(parsed["admitted_calls"]),
            cmd_lines=list(parsed["cmd_lines"]),
            ctx=dict(ctx or {}),
            invocation_mode=mode,
        )
        outputs.extend(extra_outputs)
        used.extend(extra_used)
        if outputs:
            await _record(
                record_tool_execution,
                command_text="|".join(used) or "self_callable",
                triggered=True,
                outputs=list(outputs),
                used_triggers=list(used),
            )
            return _finish_with_tools(
                outputs=outputs,
                used=used,
                messages=messages,
                invocation_mode=mode,
                fc_used=fc_used,
                cmd_used=cmd_used,
                fc_skipped_reason=fc_skipped_reason,
                sent_tool_names=sent_tool_names,
                offered_triggers=offered,
            )

        queries = list(parsed["tool_search_queries"])
        if step == 0 and discover_tools and queries:
            classified = {"tools": [], "commands": [], "callable_lines": [], "uncallable_lines": []}
            classify = getattr(plugin_manager, "classify_tool_search", None)
            prose = ""
            formatter = getattr(plugin_manager, "_format_tool_search_output", None)
            if callable(classify):
                merged_tools: list = []
                merged_commands: list[str] = []
                for query in queries:
                    one = classify(
                        query,
                        set(_CHAT_DISCOVERY_TYPES),
                        invocation_mode=mode,
                        context=dict(ctx or {}),
                    )
                    merged_tools.extend(list(one.get("tools") or []))
                    for command in one.get("commands") or []:
                        if command not in merged_commands:
                            merged_commands.append(str(command))
                classified = {
                    "tools": merged_tools,
                    "commands": merged_commands,
                }
            if callable(formatter):
                prose = "\n".join(
                    str(formatter(
                        query,
                        dict(ctx or {}),
                        set(_CHAT_DISCOVERY_TYPES),
                        invocation_mode=mode,
                    ) or "")
                    for query in queries
                ).strip()
            for command in classified.get("commands") or []:
                admitted.add(str(command))
                admitted.add(str(command).lower())
            if mode != "cmd" and classified.get("tools"):
                tools = merge_openai_tools(tools, list(classified.get("tools") or []))
            if completion.tool_calls and mode != "cmd":
                messages.append(_assistant_tool_call_message(completion))
                for call in completion.tool_calls:
                    if str(getattr(call, "name", "") or "").strip().lower() != "tool_search":
                        continue
                    messages.append(_tool_result_message(call, prose or "没有找到匹配工具。"))
            else:
                messages.append({"role": "assistant", "content": str(completion.text or "")})
                messages.append({"role": "system", "content": prose or "没有找到匹配工具。"})
            if classified.get("tools") or classified.get("commands"):
                continue
            if prose:
                await _record(
                    record_tool_execution,
                    command_text="tool_search|" + " ".join(queries),
                    triggered=True,
                    outputs=[prose],
                    used_triggers=["tool_search"],
                )
                return _finish_with_tools(
                    outputs=[prose],
                    used=["tool_search"],
                    messages=messages,
                    invocation_mode=mode,
                    fc_used=fc_used,
                    cmd_used=cmd_used,
                    fc_skipped_reason=fc_skipped_reason,
                    sent_tool_names=sent_tool_names,
                    offered_triggers=offered,
                )

        return ReactFirstPassResult(
            reply=str(completion.text or ""),
            context_messages=messages,
            invocation_mode=str(invocation_mode or "cmd"),
            fc_used=bool(fc_used),
            cmd_used=bool(cmd_used),
            fc_skipped_reason=fc_skipped_reason,
            sent_tool_names=list(sent_tool_names),
            offered_triggers=list(offered),
        )

    return ReactFirstPassResult(
        reply="",
        context_messages=messages,
        invocation_mode=str(invocation_mode or "cmd"),
        fc_used=bool(fc_used),
        cmd_used=bool(cmd_used),
        fc_skipped_reason=fc_skipped_reason,
        sent_tool_names=list(sent_tool_names),
        offered_triggers=list(offered),
    )
