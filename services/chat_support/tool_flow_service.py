"""React and delegate tool flow helpers for ChatService.process()."""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from modules.llm_types import ChatCompletionResult, ToolCallRequest
from services.chat_support.text_utils import looks_like_uncertain_answer


MAX_TOOL_RESULT_CHARS = 1500
MAX_TOOL_FEEDBACK_CHARS = 4000
TRUNCATED_MARKER = "...[truncated]"
MAX_LOOP_RESULT_CHARS = 1200
MAX_LOOP_OBSERVATION_CHARS = 3500
MAX_LOOP_OBSERVATION_ROUNDS = 2
DEFAULT_TOOL_LOOP_MAX_ITERATIONS = 3
LOOP_OBSERVATION_PREFIX = "[Tool Observation]"


class ToolTriggerStore:
    """Per-session last tool triggers. One instance, owned by tool flow."""

    def __init__(self) -> None:
        self.by_session: Dict[str, List[str]] = {}

    def get(self, session_key: str) -> list[str]:
        return list(self.by_session.get(str(session_key or "").strip()) or [])

    def remember(self, session_key: str, triggers: Sequence[str]) -> None:
        key = str(session_key or "").strip()
        remembered = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in (triggers or [])
                if str(item or "").strip()
            )
        )
        if key and remembered:
            self.by_session[key] = remembered


@dataclass(frozen=True)
class ReactFirstPassResult:
    reply: str = ""
    triggered: bool = False
    clean_thought: str = ""
    tool_results: list[str] = field(default_factory=list)
    used_triggers: list[str] = field(default_factory=list)
    context_messages: list[dict[str, Any]] = field(default_factory=list)
    invocation_mode: str = "cmd"
    fc_used: bool = False
    cmd_used: bool = False
    fc_skipped_reason: str = ""


@dataclass(frozen=True)
class ToolFinalizeResult:
    final_reply: str = ""
    final_emo: str = "neutral"
    model_emo_seen: bool = False
    context_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCommandLoopResult:
    reply: str = ""
    triggered: bool = False
    clean_thought: str = ""
    tool_results: list[str] = field(default_factory=list)
    used_triggers: list[str] = field(default_factory=list)
    context_messages: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    invocation_mode: str = "cmd"
    fc_used: bool = False
    cmd_used: bool = False
    fc_skipped_reason: str = ""


async def _call_chat_with_ai(
    chat_with_ai: Callable[..., Any],
    messages: list,
    *,
    task_type: str,
    caller: str,
    request_context: Any = None,
) -> Any:
    kwargs: Dict[str, Any] = {"task_type": task_type, "caller": caller}
    if request_context is not None:
        kwargs["request_id"] = request_context.request_id
        kwargs["call_metadata"] = {"_request_context": request_context}
    if inspect.iscoroutinefunction(chat_with_ai):
        return await chat_with_ai(messages, **kwargs)
    return await asyncio.to_thread(
        chat_with_ai,
        messages,
        **kwargs,
    )


def _as_completion(value: Any) -> ChatCompletionResult:
    if isinstance(value, ChatCompletionResult):
        return value
    return ChatCompletionResult(text=str(value or ""))


async def _call_chat_complete(
    chat_with_ai: Callable[..., Any],
    messages: list,
    *,
    task_type: str,
    caller: str,
    tools: Optional[list] = None,
    request_context: Any = None,
) -> ChatCompletionResult:
    kwargs: Dict[str, Any] = {"task_type": task_type, "caller": caller}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if request_context is not None:
        kwargs["request_id"] = request_context.request_id
        kwargs["call_metadata"] = {"_request_context": request_context}

    async def _invoke(payload: Dict[str, Any]) -> Any:
        try:
            if inspect.iscoroutinefunction(chat_with_ai):
                return await chat_with_ai(messages, **payload)
            return await asyncio.to_thread(chat_with_ai, messages, **payload)
        except TypeError:
            if "tools" not in payload:
                raise
            slim = {"task_type": payload["task_type"], "caller": payload["caller"]}
            if "request_id" in payload:
                slim["request_id"] = payload["request_id"]
            if "call_metadata" in payload:
                slim["call_metadata"] = payload["call_metadata"]
            if inspect.iscoroutinefunction(chat_with_ai):
                return await chat_with_ai(messages, **slim)
            return await asyncio.to_thread(chat_with_ai, messages, **slim)

    return _as_completion(await _invoke(kwargs))


def _assistant_tool_call_message(result: ChatCompletionResult) -> dict[str, Any]:
    tool_calls = []
    for call in result.tool_calls:
        raw_arguments = call.raw_arguments
        if not raw_arguments:
            import json

            raw_arguments = json.dumps(call.arguments or {}, ensure_ascii=False)
        tool_calls.append(
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": raw_arguments,
                },
            }
        )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": result.text or None,
        "tool_calls": tool_calls,
    }
    return message


def _without_rejected_tools(tools: Optional[list], rejected_names: set[str]) -> Optional[list]:
    if not tools or not rejected_names:
        return tools
    kept = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = str((function or {}).get("name") or "").strip()
        if name and name in rejected_names:
            continue
        kept.append(tool)
    return kept


def _outputs_for_calls(calls: Sequence[ToolCallRequest], outputs: Sequence[Any]) -> list[tuple[ToolCallRequest, str]]:
    packed = [str(item) for item in (outputs or [])]
    paired: list[tuple[ToolCallRequest, str]] = []
    for index, call in enumerate(calls):
        if index < len(packed):
            content = packed[index]
        else:
            content = f"【{call.name} 未执行】没有对应结果。"
        paired.append((call, content))
    return paired


def _confirmation_pending_text(rows: Sequence[str]) -> str:
    for row in rows:
        text = str(row or "")
        if "回复 确认" in text and "取消" in text:
            return text
    return ""


def _pending_tool_block_reply(ctx: Dict[str, Any]) -> str:
    try:
        from services.security.pending_confirm import (
            confirm_prompt,
            get_pending_confirm_store,
            session_key_from_ctx,
        )
    except Exception:
        return ""
    pending = get_pending_confirm_store().peek(session_key_from_ctx(ctx))
    if pending is None:
        return ""
    return confirm_prompt(pending.summary) + "\n还有一个操作在等确认，这次没有执行新的工具。"


def _command_names(plugin_manager: Any, text: str) -> set[str]:
    extract = getattr(plugin_manager, "extract_commands", None)
    if not callable(extract):
        return set()
    try:
        return {str(trigger or "").strip() for trigger, _args in extract(text) if str(trigger or "").strip()}
    except Exception:
        return set()


def _tool_result_message(call: ToolCallRequest, content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "content": content,
    }


def _tool_call_signature(calls: Sequence[ToolCallRequest]) -> str:
    import json

    parts = []
    for call in calls:
        parts.append(
            f"{call.name}|{json.dumps(call.arguments or {}, sort_keys=True, ensure_ascii=False)}"
        )
    return "\n".join(parts)


def project_messages_for_finalize(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for message in messages or []:
        role = str((message or {}).get("role") or "")
        if role == "tool":
            continue
        if message.get("tool_calls"):
            continue
        content = str((message or {}).get("content") or "")
        if content.startswith(LOOP_OBSERVATION_PREFIX):
            continue
        cleaned = {
            key: value
            for key, value in dict(message or {}).items()
            if key != "tool_calls"
        }
        projected.append(cleaned)
    return projected


async def _call_optional(callback: Callable[..., Any] | None, **kwargs: Any) -> None:
    if callback is None:
        return
    result = callback(**kwargs)
    if inspect.isawaitable(result):
        await result


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNCATED_MARKER) - 1)
    return text[:keep].rstrip() + "\n" + TRUNCATED_MARKER


def _compact_tool_feedback(tool_results: list[str]) -> str:
    rows = [
        _truncate_text(str(result), MAX_TOOL_RESULT_CHARS)
        for result in tool_results
    ]
    return _truncate_text("\n".join(rows), MAX_TOOL_FEEDBACK_CHARS)


def _normalize_command_args(args: str) -> str:
    return re.sub(r"\s+", " ", str(args or "").strip())


def _fallback_extract_commands(text: str) -> list[tuple[str, str]]:
    pattern = r"\[CMD:\s*([A-Za-z0-9_-]+)\s*(?:[\|／/]\s*|\s+)(.*?)\]"
    return [
        (trigger.strip(), args.strip())
        for trigger, args in re.findall(pattern, text or "", flags=re.DOTALL)
    ]


def _command_signature(plugin_manager: Any, text: str) -> str:
    extractor = getattr(plugin_manager, "extract_commands", None)
    commands = extractor(text) if callable(extractor) else _fallback_extract_commands(text)
    parts = [
        f"{str(trigger or '').strip()}|{_normalize_command_args(args)}"
        for trigger, args in commands
    ]
    return "\n".join(parts)


def _compact_loop_result(result: str) -> str:
    return _truncate_text(str(result or ""), MAX_LOOP_RESULT_CHARS)


def _compact_loop_observations(observation_rounds: list[str]) -> str:
    recent = observation_rounds[-MAX_LOOP_OBSERVATION_ROUNDS:]
    return _truncate_text("\n\n".join(recent), MAX_LOOP_OBSERVATION_CHARS)


def _without_loop_observation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = []
    for message in messages:
        content = str((message or {}).get("content") or "")
        if content.startswith(LOOP_OBSERVATION_PREFIX):
            continue
        filtered.append(message)
    return filtered


async def run_tool_command_loop(
    *,
    context_messages: list,
    ctx: Dict[str, Any],
    task_type: str,
    caller_prefix: str,
    plugin_manager: Any,
    chat_with_ai: Callable[..., Any],
    allowed_types: set[str],
    contains_cmd: Callable[[str], bool],
    record_tool_execution: Callable[..., Any] | None = None,
    max_iterations: int = DEFAULT_TOOL_LOOP_MAX_ITERATIONS,
    invocation_mode: str = "cmd",
    tools: Optional[list] = None,
    tool_triggers: Optional[Sequence[str]] = None,
    request_context: Any = None,
) -> ToolCommandLoopResult:
    messages = list(context_messages)
    tool_results: list[str] = []
    used_triggers: list[str] = []
    observation_rounds: list[str] = []
    clean_thought = ""
    final_reply = ""
    triggered = False
    last_signature = ""
    iterations = 0
    fc_used = False
    cmd_used = False
    fc_skipped_reason = ""
    rejected_names: set[str] = set()
    mode = str(invocation_mode or "cmd").strip().lower()
    if mode not in {"cmd", "dual", "function"}:
        mode = "cmd"

    request_tools = None
    if mode in {"dual", "function"}:
        if tools:
            request_tools = list(tools)
        else:
            try:
                from services.chat_support.tool_schema import build_openai_tools

                request_tools = build_openai_tools(
                    plugin_manager,
                    list(tool_triggers or []),
                    allowed_types=set(allowed_types or set()),
                    include_tool_search=True,
                )
            except Exception:
                request_tools = None
        if not request_tools:
            request_tools = None

    for index in range(max(1, int(max_iterations or 1))):
        iterations = index + 1
        active_tools = _without_rejected_tools(request_tools, rejected_names)
        completion = await _call_chat_complete(
            chat_with_ai,
            messages,
            task_type=task_type,
            caller=caller_prefix if iterations == 1 else f"{caller_prefix}_{iterations}",
            tools=active_tools,
            request_context=request_context,
        )
        if completion.fc_skipped_reason:
            fc_skipped_reason = completion.fc_skipped_reason
        final_reply = str(completion.text or "")

        if completion.tool_calls and mode in {"dual", "function"}:
            pending_reply = _pending_tool_block_reply(ctx)
            if pending_reply:
                final_reply = pending_reply
                clean_thought = pending_reply
                break
            signature = _tool_call_signature(completion.tool_calls)
            if signature and signature == last_signature:
                clean_thought = ""
                break
            last_signature = signature
            executor = getattr(plugin_manager, "execute_invocations", None)
            if not callable(executor):
                break
            command_triggered, clean_text, outputs, used = await executor(
                completion.tool_calls,
                ctx,
                allow_tools=True,
                allowed_types=set(allowed_types or set()),
                invocation_mode=mode,
            )
            await _call_optional(
                record_tool_execution,
                command_text=signature,
                triggered=bool(command_triggered),
                outputs=list(outputs or []),
                used_triggers=list(used or []),
            )
            fc_used = True
            triggered = triggered or bool(command_triggered)
            if clean_text:
                clean_thought = str(clean_text or "").strip()
            if used:
                used_triggers.extend(str(trigger) for trigger in used)
            packed = _outputs_for_calls(completion.tool_calls, outputs)
            rows = [_compact_loop_result(content) for _call, content in packed]
            for call, content in packed:
                if "未执行" in content:
                    rejected_names.add(str(call.name or "").strip())
            if rows:
                tool_results.extend(rows)
                observation_rounds.append(
                    f"Round {iterations} tool results:\n" + "\n".join(rows)
                )
            messages.append(_assistant_tool_call_message(completion))
            for call, content in packed:
                messages.append(_tool_result_message(call, _compact_loop_result(content)))
            if _confirmation_pending_text(rows):
                final_reply = _confirmation_pending_text(rows)
                clean_thought = final_reply
                break
            continue

        if mode != "function" and contains_cmd(final_reply):
            signature = _command_signature(plugin_manager, final_reply)
            if signature and signature == last_signature:
                clean_thought = ""
                break
            last_signature = signature
            command_names = _command_names(plugin_manager, final_reply)
            if command_names and command_names <= rejected_names:
                command_triggered, clean_text, outputs, used = (
                    False,
                    "",
                    [f"【{name} 未执行】本轮已拒绝过，不要再调用。" for name in sorted(command_names)],
                    [],
                )
            else:
                command_triggered, clean_text, outputs, used = await plugin_manager.execute_commands(
                final_reply,
                ctx,
                allow_tools=True,
                allowed_types=set(allowed_types or set()),
            )
            await _call_optional(
                record_tool_execution,
                command_text=final_reply,
                triggered=bool(command_triggered),
                outputs=list(outputs or []),
                used_triggers=list(used or []),
            )
            cmd_used = True
            triggered = triggered or bool(command_triggered)
            if clean_text:
                clean_thought = str(clean_text or "").strip()
            if used:
                used_triggers.extend(str(trigger) for trigger in used)
            rows = [_compact_loop_result(str(output)) for output in (outputs or [])]
            if not rows:
                rows = ["【未执行】没有返回结果。"]
            for row in rows:
                if "未执行" in row:
                    rejected_names.update(_command_names(plugin_manager, final_reply))
            tool_results.extend(rows)
            observation_rounds.append(
                f"Round {iterations} tool results:\n" + "\n".join(rows)
            )
            if _confirmation_pending_text(rows):
                final_reply = _confirmation_pending_text(rows)
                clean_thought = final_reply
                break

            messages = _without_loop_observation(messages)
            messages.append({"role": "assistant", "content": final_reply})
            if mode == "function":
                continue_hint = (
                    "Continue from this observation. If another tool step is needed, "
                    "call a provided tool. If no tool is needed, output the final "
                    "task conclusion. Do not write [CMD: in the reply body."
                )
            elif mode == "dual":
                continue_hint = (
                    "Continue from this observation. If another tool step is needed, "
                    "prefer function calling. If no tool is needed, output the final "
                    "task conclusion."
                )
            else:
                continue_hint = (
                    "Continue from this observation. If another tool step is needed, "
                    "output only the required [CMD: ...]. If no tool is needed, "
                    "output the final task conclusion."
                )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"{LOOP_OBSERVATION_PREFIX}\n"
                        f"{_compact_loop_observations(observation_rounds)}\n"
                        + continue_hint
                    ),
                }
            )
            continue

        clean_thought = final_reply.strip()
        break

    return ToolCommandLoopResult(
        reply=final_reply,
        triggered=bool(triggered),
        clean_thought=clean_thought,
        tool_results=list(tool_results),
        used_triggers=list(used_triggers),
        context_messages=project_messages_for_finalize(messages),
        iterations=iterations,
        invocation_mode=mode,
        fc_used=bool(fc_used),
        cmd_used=bool(cmd_used),
        fc_skipped_reason=str(fc_skipped_reason or ""),
    )


async def run_react_first_pass(
    *,
    context_messages: list,
    ctx: Dict[str, Any],
    need_tools: bool,
    deferred_tool_flow: bool,
    task_reasoning: str,
    task_default: str,
    plugin_manager: Any,
    chat_with_ai: Callable[..., Any],
    contains_cmd: Callable[[str], bool],
    strip_cmd_anywhere: Callable[[str], str],
    record_tool_execution: Callable[..., Any] | None = None,
    invocation_mode: str = "cmd",
    tools: Optional[list] = None,
    tool_triggers: Optional[Sequence[str]] = None,
    request_context: Any = None,
) -> ReactFirstPassResult:
    del strip_cmd_anywhere
    first_pass_task = task_reasoning if (need_tools or deferred_tool_flow) else task_default
    first_pass_caller = (
        "chat_tool_reasoning"
        if first_pass_task == task_reasoning
        else "chat_default_reply"
    )
    loop_mode = invocation_mode if first_pass_task == task_reasoning else "cmd"
    loop_result = await run_tool_command_loop(
        context_messages=list(context_messages),
        ctx=ctx,
        task_type=first_pass_task,
        caller_prefix=first_pass_caller,
        plugin_manager=plugin_manager,
        chat_with_ai=chat_with_ai,
        allowed_types={"react"},
        contains_cmd=contains_cmd,
        record_tool_execution=record_tool_execution,
        invocation_mode=loop_mode,
        tools=tools if loop_mode != "cmd" else None,
        tool_triggers=tool_triggers,
        request_context=request_context,
    )
    return ReactFirstPassResult(
        reply=str(loop_result.reply or ""),
        triggered=bool(loop_result.triggered),
        clean_thought=str(loop_result.clean_thought or ""),
        tool_results=list(loop_result.tool_results or []),
        used_triggers=list(loop_result.used_triggers or []),
        context_messages=list(loop_result.context_messages or []),
        invocation_mode=str(loop_result.invocation_mode or loop_mode),
        fc_used=bool(loop_result.fc_used),
        cmd_used=bool(loop_result.cmd_used),
        fc_skipped_reason=str(loop_result.fc_skipped_reason or ""),
    )


async def finalize_tool_reply(
    *,
    clean_thought: str,
    tool_results: list[str],
    used_triggers: list[str],
    context_messages: list,
    route_reason: str,
    task_default: str,
    start_emo: str,
    chat_with_ai: Callable[..., Any],
    extract_emo_tag: Callable[[str], tuple[str, str]],
    character_sharing_enabled: bool,
    try_share: Callable[[], str],
    is_model_error_reply: Callable[[str], bool] | None = None,
) -> ToolFinalizeResult:
    messages = list(context_messages)
    final_reply = ""
    final_emo = start_emo
    model_emo_seen = False

    _, clean_first = extract_emo_tag(clean_thought or "")
    if looks_like_uncertain_answer(clean_first):
        clean_first = ""
    if clean_first:
        messages.append({"role": "assistant", "content": clean_first})

    feedback = _compact_tool_feedback(tool_results)
    compact_hint = ""
    if used_triggers:
        used_set = {str(trigger or "").strip().lower() for trigger in used_triggers}
        today = datetime.now().strftime("%Y-%m-%d")
        if used_set & {"search", "search_web"}:
            compact_hint = (
                "\n只根据工具结果回答当前问题，不要引用更早轮次的夸奖、称呼或闲聊。"
                "只输出关键信息，最多 3 条；不要表格，不要展示思考过程，"
                "不要输出完整链接。行情、价格、汇率、指数问题尽量给出具体数值、单位和时间。"
                f"今天是 {today}，「今天」「现在」以这个日期为准；结果里如果是去年或更早，不要当成今天。"
            )
        elif (
            used_set & {"workspace_ops"}
            and str(route_reason or "")
            in {"workspace_read_preferred", "capability:workspace.read"}
        ):
            compact_hint = (
                "\n仅基于文件内容回答：先说明这是什么文件、主要做什么；"
                "不要延伸诊断用户未明确提出的问题，不要补充无直接依据的建议。"
            )
        elif used_set & {"info_gateway"}:
            compact_hint = (
                "\n只基于工具结果回答；如果工具结果表示没有返回可用数据或调用失败，"
                "明确说天气接口本次暂时不可用或没有返回数据，不要说自己没有这个功能，"
                "不要猜测天气、温度、新闻或其他具体信息。"
                f"工具结果里的观测时间就是本次实况时间；今天是 {today}，按这个日期说，不要改成去年。"
            )

    if compact_hint != "__direct_result__":
        messages.append(
            {
                "role": "system",
                "content": f"【系统反馈】工具结果：\n{feedback}{compact_hint}\n请据此回答。",
            }
        )
        reply_final = await _call_chat_with_ai(
            chat_with_ai,
            messages,
            task_type=task_default,
            caller="chat_tool_finalize",
        )
        emo_final, clean_final = extract_emo_tag(reply_final or "")
        finalize_failed = bool(
            callable(is_model_error_reply)
            and is_model_error_reply(str(reply_final or ""))
        )
        search_result_available = bool(
            feedback.strip()
            and {str(trigger or "").strip().lower() for trigger in used_triggers}
            & {"search", "search_web"}
        )
        if search_result_available and (finalize_failed or not clean_final.strip()):
            final_reply = feedback.strip()
            final_emo = start_emo
            model_emo_seen = True
        else:
            final_reply = clean_final.strip() or clean_first
            final_emo = emo_final or start_emo
            model_emo_seen = bool(emo_final)

    if character_sharing_enabled and compact_hint != "__direct_result__":
        sharing = try_share()
        if sharing:
            final_reply = f"{final_reply}\n\n{sharing}"

    return ToolFinalizeResult(
        final_reply=final_reply,
        final_emo=final_emo,
        model_emo_seen=model_emo_seen,
        context_messages=messages,
    )
