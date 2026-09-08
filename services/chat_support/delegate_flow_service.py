"""Delegate flow decision helpers for ChatService.process()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence

from services.chat_support.tool_flow_service import run_tool_command_loop


DELEGATE_MODE_NONE = "none"
DELEGATE_MODE_SEARCH = "search"
DELEGATE_MODE_WORKSPACE_READ = "workspace_read"
DELEGATE_MODE_BACKGROUND = "background"
DELEGATE_MODE_ROUND = "round"
SEARCH_NOT_STARTED_MESSAGE = "联网搜索未能启动，请检查搜索插件是否启用及当前来源权限。"


@dataclass(frozen=True)
class DelegateExecutionDecision:
    mode: str = DELEGATE_MODE_NONE
    background_delegate: bool = False


@dataclass(frozen=True)
class DelegateFlowResult:
    mode: str = DELEGATE_MODE_NONE
    triggered: bool = False
    clean: str = ""
    results: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    background_delegate: bool = False


def _non_empty_triggers(delegate_triggers: Sequence[str]) -> list[str]:
    return [
        str(trigger or "").strip()
        for trigger in delegate_triggers
        if str(trigger or "").strip()
    ]


def _is_workspace_read_reason(route_reason: str) -> bool:
    return str(route_reason or "") in {
        "workspace_read_preferred",
        "capability:workspace.read",
    }


def should_use_background_delegate(
    *,
    route_reason: str,
    delegate_triggers: Sequence[str],
    ctx: Optional[Dict[str, Any]],
    is_search_delegate: Callable[[list[str], str], bool],
) -> bool:
    triggers = _non_empty_triggers(delegate_triggers)
    if not triggers:
        return False
    if _is_workspace_read_reason(route_reason):
        return False
    if is_search_delegate(triggers, ""):
        return False
    source = str((ctx or {}).get("source") or "").strip().lower()
    return source == "text_input"


def choose_delegate_execution(
    *,
    route_reason: str,
    delegate_triggers: Sequence[str],
    ctx: Optional[Dict[str, Any]],
    user_text: str,
    followup_search_query: str,
    is_search_delegate: Callable[[list[str], str], bool],
) -> DelegateExecutionDecision:
    triggers = _non_empty_triggers(delegate_triggers)
    if not triggers:
        return DelegateExecutionDecision()
    if is_search_delegate(triggers, str(user_text or "")):
        return DelegateExecutionDecision(mode=DELEGATE_MODE_SEARCH)
    if _is_workspace_read_reason(route_reason):
        return DelegateExecutionDecision(mode=DELEGATE_MODE_WORKSPACE_READ)
    background_delegate = should_use_background_delegate(
        route_reason=route_reason,
        delegate_triggers=triggers,
        ctx=ctx,
        is_search_delegate=is_search_delegate,
    )
    if background_delegate:
        return DelegateExecutionDecision(
            mode=DELEGATE_MODE_BACKGROUND,
            background_delegate=True,
        )
    return DelegateExecutionDecision(mode=DELEGATE_MODE_ROUND)


async def run_workspace_read_shortcut(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    plugin_manager: Any,
    extract_workspace_read_path: Callable[[str], str],
) -> DelegateFlowResult:
    path = extract_workspace_read_path(user_text)
    if not path:
        return DelegateFlowResult(mode=DELEGATE_MODE_WORKSPACE_READ)
    delegate_ctx = dict(ctx or {})
    delegate_ctx["delegate_mode"] = True
    delegate_ctx["allow_read"] = True
    delegate_ctx["allow_write"] = False
    delegate_ctx["allow_exec"] = False
    command = f"[CMD: workspace_ops | read_file ||| {path}]"
    triggered, clean, results, used = await plugin_manager.execute_commands(
        command,
        delegate_ctx,
        allow_tools=True,
        allowed_types={"delegate"},
    )
    return DelegateFlowResult(
        mode=DELEGATE_MODE_WORKSPACE_READ,
        triggered=bool(triggered),
        clean=str(clean or ""),
        results=list(results or []),
        used=list(used or []),
    )


def _delegate_contains_cmd(plugin_manager: Any) -> Callable[[str], bool]:
    contains_cmd = getattr(plugin_manager, "contains_cmd", None)
    if callable(contains_cmd):
        return contains_cmd
    return lambda text: "[CMD:" in str(text or "")


async def run_delegate_round(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    context_messages: list,
    delegate_triggers: Sequence[str],
    task_reasoning: str,
    plugin_manager: Any,
    chat_with_ai: Callable[..., Any],
    logger: Any = None,
    invocation_mode: str = "cmd",
    tools: list | None = None,
) -> DelegateFlowResult:
    triggers = _non_empty_triggers(delegate_triggers)
    if not triggers:
        return DelegateFlowResult(mode=DELEGATE_MODE_ROUND)

    delegate_prompt = plugin_manager.get_delegate_prompt_for_triggers(
        list(triggers),
        compact=True,
        invocation_mode=invocation_mode,
    )
    if not delegate_prompt:
        return DelegateFlowResult(mode=DELEGATE_MODE_ROUND)

    delegate_ctx = dict(ctx or {})
    delegate_ctx["delegate_mode"] = True
    delegate_ctx["allow_read"] = True
    delegate_ctx["allow_write"] = bool(delegate_ctx.get("allow_write", False))
    delegate_ctx["allow_exec"] = bool(delegate_ctx.get("allow_exec", False))

    delegate_messages = list(context_messages)
    delegate_messages.append(
        {
            "role": "system",
            "content": (
                "【副脑模式】你当前是任务执行脑，只负责为复杂任务选择并调用委托型工具。"
                "不要维持人设聊天，不要安抚，不要寒暄，只输出必要的工具调用或极简任务结论。\n"
                + delegate_prompt
            ),
        }
    )
    mode = str(invocation_mode or "cmd").strip().lower()
    if mode == "function":
        tool_instruction = "如果需要，调用提供的 tools。不要在正文写 [CMD:。"
    elif mode == "dual":
        tool_instruction = "如果需要，优先使用 function calling。不要在正文夹带 [CMD:。"
    else:
        tool_instruction = "如果需要，严格输出对应的 [CMD: 命令 | 需求说明]。"
    delegate_messages.append(
        {
            "role": "user",
            "content": (
                "请判断这条请求是否需要委托型工具。"
                f"{tool_instruction}"
                "如果不需要，输出一句不超过20字的结论。\n\n"
                f"原始请求：{user_text}"
            ),
        }
    )

    try:
        loop_result = await run_tool_command_loop(
            context_messages=delegate_messages,
            ctx=delegate_ctx,
            task_type=task_reasoning,
            caller_prefix="chat_delegate_reasoning",
            plugin_manager=plugin_manager,
            chat_with_ai=chat_with_ai,
            allowed_types={"delegate"},
            contains_cmd=_delegate_contains_cmd(plugin_manager),
            invocation_mode=invocation_mode,
            tools=tools,
            tool_triggers=list(triggers),
        )
        if logger:
            logger.info(
                f"[Delegate] loop result: reply={repr(str(loop_result.reply or '')[:80])}, "
                f"triggered={loop_result.triggered}, used={loop_result.used_triggers}, "
                f"outputs={len(loop_result.tool_results)}, iterations={loop_result.iterations}"
            )
    except Exception as exc:
        err = str(exc or "").strip()
        lowered = err.lower()
        if "429" in lowered or "rate limit" in lowered:
            return DelegateFlowResult(
                mode=DELEGATE_MODE_ROUND,
                triggered=True,
                results=["副脑当前请求过多，暂时无法执行复杂任务，请稍后再试。"],
            )
        return DelegateFlowResult(
            mode=DELEGATE_MODE_ROUND,
            triggered=True,
            results=[f"副脑执行复杂任务时失败：{err or '未知错误'}"],
        )

    return DelegateFlowResult(
        mode=DELEGATE_MODE_ROUND,
        triggered=bool(loop_result.triggered),
        clean=str(loop_result.clean_thought or ""),
        results=list(loop_result.tool_results or []),
        used=list(loop_result.used_triggers or []),
    )


async def run_delegate_flow(
    *,
    decision: DelegateExecutionDecision,
    user_text: str,
    ctx: Dict[str, Any],
    context_messages: list,
    delegate_triggers: Sequence[str],
    task_reasoning: str,
    plugin_manager: Any,
    chat_with_ai: Callable[..., Any],
    extract_workspace_read_path: Callable[[str], str],
    run_search_delegate_query: Optional[Callable[..., Any]],
    followup_search_query: str,
    logger: Any = None,
    invocation_mode: str = "cmd",
    tools: list | None = None,
) -> DelegateFlowResult:
    if decision.mode == DELEGATE_MODE_SEARCH:
        if run_search_delegate_query is None:
            return DelegateFlowResult(mode=DELEGATE_MODE_SEARCH)
        query = str(followup_search_query or "").strip() or str(user_text or "").strip()
        triggered, clean, results, used = await run_search_delegate_query(
            query=query,
            ctx=ctx,
        )
        if not triggered and not results:
            triggered = True
            results = [SEARCH_NOT_STARTED_MESSAGE]
            used = ["search_web"]
        return DelegateFlowResult(
            mode=DELEGATE_MODE_SEARCH,
            triggered=bool(triggered),
            clean=str(clean or ""),
            results=list(results or []),
            used=list(used or []),
        )
    if decision.mode == DELEGATE_MODE_WORKSPACE_READ:
        return await run_workspace_read_shortcut(
            user_text=user_text,
            ctx=ctx,
            plugin_manager=plugin_manager,
            extract_workspace_read_path=extract_workspace_read_path,
        )
    if decision.mode == DELEGATE_MODE_BACKGROUND:
        return DelegateFlowResult(
            mode=DELEGATE_MODE_BACKGROUND,
            triggered=True,
            results=["我先在后台处理，完成后再回来告诉你结果。"],
            background_delegate=True,
        )
    if decision.mode == DELEGATE_MODE_ROUND:
        return await run_delegate_round(
            user_text=user_text,
            ctx=ctx,
            context_messages=context_messages,
            delegate_triggers=delegate_triggers,
            task_reasoning=task_reasoning,
            plugin_manager=plugin_manager,
            chat_with_ai=chat_with_ai,
            logger=logger,
            invocation_mode=invocation_mode,
            tools=tools,
        )
    return DelegateFlowResult(mode=DELEGATE_MODE_NONE)


async def run_background_delegate_task(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    context_messages: list,
    delegate_triggers: Sequence[str],
    task_reasoning: str,
    transcript_meta: Dict[str, Any],
    chat_log_source: str,
    output_profile: Dict[str, Any],
    plugin_manager: Any,
    chat_with_ai: Callable[..., Any],
    set_delegate_task_state: Callable[..., None],
    add_delegate_session_event: Callable[..., None],
    should_fallback_from_moegirl: Callable[..., bool],
    polish_background_delegate_reply: Callable[..., Awaitable[Any]],
    emit_assistant_text: Callable[..., Awaitable[Any]],
    logger: Any = None,
    invocation_mode: str = "cmd",
) -> None:
    from services.chat_support import output_coordinator, search_flow_service

    triggers = list(delegate_triggers or [])
    try:
        set_delegate_task_state(
            ctx,
            "running",
            summary=user_text[:200],
            triggers=triggers,
            meta={"background": True},
        )
        add_delegate_session_event(
            "background_running",
            ctx=ctx,
            user_text=user_text,
            triggers=triggers,
            text=user_text,
            meta={"background": True},
        )
        delegate_flow_result = await run_delegate_round(
            user_text=user_text,
            ctx=ctx,
            context_messages=context_messages,
            delegate_triggers=triggers,
            task_reasoning=task_reasoning,
            plugin_manager=plugin_manager,
            chat_with_ai=chat_with_ai,
            logger=logger,
            invocation_mode=invocation_mode,
        )
        delegate_triggered = delegate_flow_result.triggered
        delegate_clean = delegate_flow_result.clean
        delegate_results = list(delegate_flow_result.results or [])
        delegate_used = list(delegate_flow_result.used or [])
        if "moegirl_wiki" in set(delegate_used or triggers):
            if should_fallback_from_moegirl(delegate_results):
                fallback_results, fallback_used = (
                    await search_flow_service.run_search_fallback_for_moegirl(
                        user_text=user_text,
                        ctx=ctx,
                        plugin_manager=plugin_manager,
                    )
                )
                if fallback_results:
                    merged_results = list(delegate_results or [])
                    merged_results.append("\n【萌百未给出明确主词条，以下是联网补充】")
                    merged_results.extend(fallback_results)
                    delegate_results = merged_results
                if fallback_used:
                    delegate_used = list(
                        dict.fromkeys(list(delegate_used or []) + list(fallback_used))
                    )
        summary = (
            "\n".join(delegate_results)[:200]
            if delegate_results
            else (delegate_clean or user_text[:200])
        )
        set_delegate_task_state(
            ctx,
            "done" if delegate_results or delegate_used else "skipped",
            summary=summary,
            triggers=triggers,
            meta={
                "background": True,
                "delegate_triggered": bool(delegate_triggered),
                "delegate_used": list(delegate_used or []),
            },
        )
        add_delegate_session_event(
            "background_completed"
            if (delegate_results or delegate_used)
            else "background_skipped",
            ctx=ctx,
            user_text=user_text,
            triggers=triggers,
            text=(
                "\n".join(delegate_results)[:600]
                if delegate_results
                else (delegate_clean or "")
            ),
            meta={
                "background": True,
                "delegate_used": list(delegate_used or []),
            },
        )
        final_text = ""
        if delegate_results:
            final_text = "\n".join(
                str(item) for item in delegate_results if str(item).strip()
            )
        elif delegate_clean:
            final_text = delegate_clean
        if not final_text:
            final_text = "后台任务已处理完成。"
        final_text, final_emo = await polish_background_delegate_reply(
            user_text=user_text,
            delegate_triggers=triggers,
            delegate_results=delegate_results,
            delegate_clean=delegate_clean,
        )
        await output_coordinator.emit_background_delegate_reply(
            text=final_text,
            emotion=final_emo,
            ctx=ctx,
            transcript_meta=transcript_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            emit_assistant_text=emit_assistant_text,
        )
        add_delegate_session_event(
            "background_reply",
            ctx=ctx,
            user_text=user_text,
            triggers=triggers,
            text=final_text,
            meta={"emotion": final_emo, "background": True},
        )
    except Exception as exc:
        set_delegate_task_state(
            ctx,
            "failed",
            summary=str(exc)[:200],
            triggers=triggers,
            meta={"background": True},
        )
        add_delegate_session_event(
            "background_failed",
            ctx=ctx,
            user_text=user_text,
            triggers=triggers,
            text=str(exc),
            meta={"background": True},
        )
        from services.chat_support import output_coordinator

        await output_coordinator.emit_background_delegate_reply(
            text=f"刚才委托的后台任务失败了：{exc}",
            emotion="neutral",
            ctx=ctx,
            transcript_meta=transcript_meta,
            chat_log_source=chat_log_source,
            output_profile=output_profile,
            emit_assistant_text=emit_assistant_text,
        )
