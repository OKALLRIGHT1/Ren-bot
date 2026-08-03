"""Delegate flow decision helpers for ChatService.process()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence

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
) -> DelegateFlowResult:
    triggers = _non_empty_triggers(delegate_triggers)
    if not triggers:
        return DelegateFlowResult(mode=DELEGATE_MODE_ROUND)

    delegate_prompt = plugin_manager.get_delegate_prompt_for_triggers(
        list(triggers), compact=True
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
    delegate_messages.append(
        {
            "role": "user",
            "content": (
                "请判断这条请求是否需要委托型工具。"
                "如果需要，严格输出对应的 [CMD: 命令 | 需求说明]。"
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
        )
    return DelegateFlowResult(mode=DELEGATE_MODE_NONE)
