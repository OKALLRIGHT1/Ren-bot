"""React and delegate tool flow helpers for ChatService.process()."""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from services.chat_support.text_utils import looks_like_uncertain_answer


MAX_TOOL_RESULT_CHARS = 1500
MAX_TOOL_FEEDBACK_CHARS = 4000
TRUNCATED_MARKER = "...[truncated]"
MAX_LOOP_RESULT_CHARS = 1200
MAX_LOOP_OBSERVATION_CHARS = 3500
MAX_LOOP_OBSERVATION_ROUNDS = 2
DEFAULT_TOOL_LOOP_MAX_ITERATIONS = 3
LOOP_OBSERVATION_PREFIX = "[Tool Observation]"


@dataclass(frozen=True)
class ReactFirstPassResult:
    reply: str = ""
    triggered: bool = False
    clean_thought: str = ""
    tool_results: list[str] = field(default_factory=list)
    used_triggers: list[str] = field(default_factory=list)
    context_messages: list[dict[str, Any]] = field(default_factory=list)


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


async def _call_chat_with_ai(
    chat_with_ai: Callable[..., Any],
    messages: list,
    *,
    task_type: str,
    caller: str,
) -> Any:
    if inspect.iscoroutinefunction(chat_with_ai):
        return await chat_with_ai(messages, task_type=task_type, caller=caller)
    return await asyncio.to_thread(
        chat_with_ai,
        messages,
        task_type=task_type,
        caller=caller,
    )


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

    for index in range(max(1, int(max_iterations or 1))):
        iterations = index + 1
        reply = await _call_chat_with_ai(
            chat_with_ai,
            messages,
            task_type=task_type,
            caller=caller_prefix if iterations == 1 else f"{caller_prefix}_{iterations}",
        )
        final_reply = str(reply or "")

        if not contains_cmd(final_reply):
            clean_thought = final_reply.strip()
            break

        signature = _command_signature(plugin_manager, final_reply)
        if signature and signature == last_signature:
            clean_thought = ""
            break
        last_signature = signature

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
        triggered = triggered or bool(command_triggered)
        if clean_text:
            clean_thought = str(clean_text or "").strip()
        if used:
            used_triggers.extend(str(trigger) for trigger in used)
        if outputs:
            rows = [_compact_loop_result(str(output)) for output in outputs]
            tool_results.extend(rows)
            observation_rounds.append(
                f"Round {iterations} tool results:\n" + "\n".join(rows)
            )
        else:
            break

        messages = _without_loop_observation(messages)
        messages.append({"role": "assistant", "content": final_reply})
        messages.append(
            {
                "role": "system",
                "content": (
                    f"{LOOP_OBSERVATION_PREFIX}\n"
                    f"{_compact_loop_observations(observation_rounds)}\n"
                    "Continue from this observation. If another tool step is needed, "
                    "output only the required [CMD: ...]. If no tool is needed, "
                    "output the final task conclusion."
                ),
            }
        )

    return ToolCommandLoopResult(
        reply=final_reply,
        triggered=bool(triggered),
        clean_thought=clean_thought,
        tool_results=list(tool_results),
        used_triggers=list(used_triggers),
        context_messages=messages,
        iterations=iterations,
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
) -> ReactFirstPassResult:
    del strip_cmd_anywhere
    first_pass_task = task_reasoning if (need_tools or deferred_tool_flow) else task_default
    first_pass_caller = (
        "chat_tool_reasoning"
        if first_pass_task == task_reasoning
        else "chat_default_reply"
    )
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
    )
    return ReactFirstPassResult(
        reply=str(loop_result.reply or ""),
        triggered=bool(loop_result.triggered),
        clean_thought=str(loop_result.clean_thought or ""),
        tool_results=list(loop_result.tool_results or []),
        used_triggers=list(loop_result.used_triggers or []),
        context_messages=list(loop_result.context_messages or []),
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
        if used_set & {"claw_email"}:
            final_reply = feedback
            final_emo = "neutral"
            model_emo_seen = True
            if len(final_reply) > 1800:
                final_reply = final_reply[:1800].rstrip() + "\n..."
            compact_hint = "__direct_result__"
        elif used_set & {"search", "search_web"}:
            compact_hint = (
                "\n只根据工具结果回答当前问题，不要引用更早轮次的夸奖、称呼或闲聊。"
                "只输出关键信息，最多 3 条；不要表格，不要展示思考过程，"
                "不要输出完整链接。行情、价格、汇率、指数问题尽量给出具体数值、单位和时间。"
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
