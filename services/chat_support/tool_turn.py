"""Non-stream tool/search/delegate turn. process() must not unpack first-pass fields."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from services.capability_gatekeeper import (
    build_forced_capability_command,
    refine_capability_args,
)
from services.capability_manager import is_force_executable_capability
from services.chat_support import (
    delegate_flow_service,
    reply_flow_service,
    search_flow_service,
    tool_flow_service,
)
from services.chat_support.tool_flow_service import ReactFirstPassResult, ToolTriggerStore
from services.chat_support.tool_invocation import (
    collect_invocation_args,
    missing_required_arguments,
    missing_required_message,
)


@dataclass
class ToolTurnHooks:
    plugin_manager: Any
    loop_chat: Callable[..., Any]
    plain_chat: Callable[..., Any]
    contains_cmd: Callable[[str], bool]
    strip_cmd_anywhere: Callable[[str], str]
    strip_emo_tags_anywhere: Callable[[str], str]
    clean_text_for_tts: Callable[[str], str]
    extract_emo_tag: Callable[[str], tuple[str, str]]
    try_share: Callable[[], str]
    looks_like_uncertain_answer: Callable[[str], bool]
    is_searchworthy_question: Callable[[str], bool]
    is_search_delegate: Callable[..., bool]
    extract_workspace_read_path: Callable[[str], str]
    record_tool_execution: Callable[..., Any]
    set_delegate_task_state: Callable[..., None]
    add_delegate_session_event: Callable[..., None]
    queue_background_delegate: Callable[..., None]
    add_codex_session_event: Callable[..., None]
    add_memory_safe: Callable[..., Awaitable[Any]]
    apply_forbidden_guard: Callable[..., Awaitable[tuple[str, Dict[str, Any]]]]
    reply_start_emotion: Callable[..., tuple[str, float]]
    looks_like_upstream_error: Callable[[str], bool]
    strip_internal_tags: Callable[[str], str]
    should_suppress_followup_preface: Callable[[str], bool]
    merge_preface_texts: Callable[..., str]
    apply_character_catchphrase: Callable[[str], str]
    prepare_reply_for_output: Callable[..., str]
    infer_reply_emotion_with_llm: Callable[..., Awaitable[Optional[str]]]
    remember_search_topic: Callable[..., None]
    trace_process: Optional[Callable[..., None]] = None
    logger: Any = None


@dataclass
class NonStreamTurnResult:
    reply: str = ""
    emotion: str = "neutral"
    triggered: bool = False
    tool_event_state: Dict[str, str] = field(default_factory=dict)
    start_emo: str = "neutral"
    start_intensity: float = 0.4
    background_queued: bool = False


def should_force_capability_route(
    *,
    route_reason: str,
    normal_triggers: Sequence[str],
    used_triggers: Sequence[str],
) -> bool:
    if not is_force_executable_capability(route_reason):
        return False
    selected = {str(trigger or "") for trigger in (normal_triggers or [])}
    if not selected:
        return False
    executed = {str(trigger or "") for trigger in (used_triggers or [])}
    return not bool(selected & executed)


def _is_search_trigger(trigger: str) -> bool:
    return str(trigger or "").strip().lower() in {"search", "search_web"}


async def _apply_forced_capability(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    route: Any,
    normal_triggers: Sequence[str],
    used_triggers: list[str],
    tool_results: list[str],
    clean_thought: str,
    hooks: ToolTurnHooks,
) -> tuple[bool, str, list[str], list[str]]:
    triggered = False
    forced_trigger = str((list(normal_triggers) or [""])[0] or "").strip()
    capability_id = str(getattr(route, "capability_id", "") or "").strip()
    capability_args = dict(getattr(route, "capability_args", None) or {})
    gate_decision = await refine_capability_args(
        user_text=user_text,
        capability_id=capability_id,
        initial_args=capability_args,
        chat_with_ai=hooks.plain_chat,
    )
    if gate_decision is not None and gate_decision.approved:
        capability_args = dict(gate_decision.args or capability_args)
    plugin = None
    manager = hooks.plugin_manager
    if manager is not None:
        plugin = (
            (getattr(manager, "react_map", None) or {}).get(forced_trigger)
            or (getattr(manager, "delegate_map", None) or {}).get(forced_trigger)
            or (getattr(manager, "plugins", None) or {}).get(forced_trigger)
        )
    collected_args = collect_invocation_args(
        plugin,
        user_text=user_text,
        capability_id=capability_id,
        initial_args=capability_args,
    )
    missing_fields = missing_required_arguments(plugin, collected_args)
    if missing_fields:
        used_triggers.append(forced_trigger)
        tool_results.append(missing_required_message(forced_trigger, missing_fields))
        return True, clean_thought, tool_results, used_triggers
    forced_capability_cmd = build_forced_capability_command(
        trigger=forced_trigger,
        user_text=user_text,
        capability_id=capability_id,
        capability_args=capability_args,
    )
    (
        capability_triggered,
        capability_clean,
        capability_results,
        capability_used,
    ) = await manager.execute_commands(
        forced_capability_cmd,
        ctx,
        allow_tools=True,
        allowed_types={"react", "delegate"},
    )
    if capability_clean and not clean_thought:
        clean_thought = capability_clean
    if capability_triggered:
        triggered = True
    if capability_results:
        tool_results.extend(capability_results)
    if capability_used:
        used_triggers.extend(capability_used)
    return triggered, clean_thought, tool_results, used_triggers


async def run_non_stream_tool_turn(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    context_messages: list,
    route: Any,
    need_tools: bool,
    deferred_tool_flow: bool,
    normal_triggers: Sequence[str],
    delegate_triggers: Sequence[str],
    effective_triggers: Sequence[str],
    followup_search_query: str,
    search_delegate_requested: bool,
    task_reasoning: str,
    task_default: str,
    invocation_mode: str,
    session_key: str,
    memory_session_id: Optional[str],
    preface_text: str,
    transcript_channel_meta: Dict[str, Any],
    chat_log_source: str,
    output_profile: Dict[str, Any],
    hooks: ToolTurnHooks,
    tool_trigger_store: ToolTriggerStore,
    character_sharing_enabled: bool = False,
    existing_first_pass: Optional[ReactFirstPassResult] = None,
    existing_tool_event_state: Optional[Dict[str, str]] = None,
) -> NonStreamTurnResult:
    tool_event_state = dict(existing_tool_event_state or {})
    if existing_first_pass is not None:
        react_first_pass = existing_first_pass
    elif search_delegate_requested:
        react_first_pass = ReactFirstPassResult(
            context_messages=list(context_messages),
            invocation_mode=invocation_mode,
        )
    else:
        react_first_pass = await tool_flow_service.run_react_first_pass(
            context_messages=context_messages,
            ctx=ctx,
            need_tools=need_tools,
            deferred_tool_flow=deferred_tool_flow,
            task_reasoning=task_reasoning,
            task_default=task_default,
            plugin_manager=hooks.plugin_manager,
            chat_with_ai=hooks.loop_chat,
            contains_cmd=hooks.contains_cmd,
            strip_cmd_anywhere=hooks.strip_cmd_anywhere,
            record_tool_execution=lambda **event: hooks.record_tool_execution(
                event_state=tool_event_state,
                **event,
            ),
            invocation_mode=invocation_mode,
            tool_triggers=list(normal_triggers or []),
        )

    reply1 = react_first_pass.reply
    triggered = bool(react_first_pass.triggered)
    clean_thought = str(react_first_pass.clean_thought or "")
    tool_results = list(react_first_pass.tool_results or [])
    used_triggers = list(react_first_pass.used_triggers or [])
    context_messages = list(react_first_pass.context_messages or context_messages)
    if hooks.trace_process:
        hooks.trace_process(
            "tool_loop",
            invocation_mode=str(getattr(react_first_pass, "invocation_mode", "") or invocation_mode),
            fc_used=bool(getattr(react_first_pass, "fc_used", False)),
            cmd_used=bool(getattr(react_first_pass, "cmd_used", False)),
            fc_skipped_reason=str(getattr(react_first_pass, "fc_skipped_reason", "") or ""),
        )

    if should_force_capability_route(
        route_reason=str(getattr(route, "reason", "") or ""),
        normal_triggers=normal_triggers or [],
        used_triggers=used_triggers,
    ):
        cap_triggered, clean_thought, tool_results, used_triggers = (
            await _apply_forced_capability(
                user_text=user_text,
                ctx=ctx,
                route=route,
                normal_triggers=normal_triggers,
                used_triggers=used_triggers,
                tool_results=tool_results,
                clean_thought=clean_thought,
                hooks=hooks,
            )
        )
        triggered = triggered or cap_triggered

    forced_search_query = ""
    if not triggered and not tool_results and not delegate_triggers:
        forced_search_query = search_flow_service.choose_forced_search_query(
            user_text=user_text,
            first_reply=reply1 or "",
            followup_query=followup_search_query,
            triggered=triggered,
            tool_results=tool_results,
            delegate_triggers=delegate_triggers,
            looks_like_uncertain_answer=hooks.looks_like_uncertain_answer,
            is_searchworthy_question=hooks.is_searchworthy_question,
        )
        if forced_search_query:
            (
                search_triggered,
                search_clean,
                search_results,
                search_used,
            ) = await search_flow_service.run_search_delegate_query(
                query=forced_search_query,
                ctx=ctx,
                plugin_manager=hooks.plugin_manager,
            )
            if search_clean and not clean_thought:
                clean_thought = search_clean
            if search_triggered:
                triggered = True
            if search_results:
                tool_results.extend(search_results)
            if search_used:
                used_triggers.extend(search_used)

    background_queued = False
    if delegate_triggers:
        hooks.set_delegate_task_state(
            ctx,
            "running",
            summary=user_text[:200],
            triggers=delegate_triggers,
        )
        hooks.add_delegate_session_event(
            "running",
            ctx=ctx,
            user_text=user_text,
            triggers=delegate_triggers,
            text=user_text,
        )
        delegate_decision = delegate_flow_service.choose_delegate_execution(
            route_reason=str(getattr(route, "reason", "") or ""),
            delegate_triggers=delegate_triggers,
            ctx=ctx,
            user_text=user_text,
            followup_search_query=followup_search_query,
            is_search_delegate=hooks.is_search_delegate,
        )
        delegate_flow_result = await delegate_flow_service.run_delegate_flow(
            decision=delegate_decision,
            user_text=user_text,
            ctx=ctx,
            context_messages=context_messages,
            delegate_triggers=delegate_triggers,
            task_reasoning=task_reasoning,
            plugin_manager=hooks.plugin_manager,
            chat_with_ai=hooks.loop_chat,
            extract_workspace_read_path=hooks.extract_workspace_read_path,
            run_search_delegate_query=lambda *, query, ctx: search_flow_service.run_search_delegate_query(
                query=query,
                ctx=ctx,
                plugin_manager=hooks.plugin_manager,
            ),
            followup_search_query=followup_search_query,
            logger=hooks.logger,
            invocation_mode=invocation_mode,
        )
        delegate_triggered = delegate_flow_result.triggered
        delegate_clean = delegate_flow_result.clean
        delegate_results = delegate_flow_result.results
        delegate_used = delegate_flow_result.used
        background_delegate = delegate_flow_result.background_delegate
        if background_delegate:
            background_queued = True
            hooks.set_delegate_task_state(
                ctx,
                "queued",
                summary=user_text[:200],
                triggers=delegate_triggers,
                meta={"background": True},
            )
            hooks.add_delegate_session_event(
                "queued",
                ctx=ctx,
                user_text=user_text,
                triggers=delegate_triggers,
                text="后台排队中",
                meta={"background": True},
            )
            hooks.queue_background_delegate(
                user_text=user_text,
                ctx=dict(ctx or {}),
                context_messages=list(context_messages),
                delegate_triggers=list(delegate_triggers),
                task_reasoning=task_reasoning,
                transcript_meta=dict(transcript_channel_meta or {}),
                chat_log_source=chat_log_source,
                output_profile=dict(output_profile or {}),
            )
        if delegate_clean and not clean_thought:
            clean_thought = delegate_clean
        if delegate_triggered:
            triggered = True
        if delegate_results:
            tool_results.extend(delegate_results)
        if delegate_used:
            used_triggers.extend(delegate_used)
        hooks.set_delegate_task_state(
            ctx,
            (
                "queued"
                if background_delegate
                else ("done" if delegate_results or delegate_used else "skipped")
            ),
            summary=(
                "\n".join(delegate_results)[:200]
                if delegate_results
                else (delegate_clean or user_text[:200])
            ),
            triggers=delegate_triggers,
            meta={
                "background": bool(background_delegate),
                "delegate_triggered": bool(delegate_triggered),
                "delegate_used": list(delegate_used or []),
            },
        )
        hooks.add_delegate_session_event(
            "completed" if (delegate_results or delegate_used) else "skipped",
            ctx=ctx,
            user_text=user_text,
            triggers=delegate_triggers,
            text=(
                "\n".join(delegate_results)[:600]
                if delegate_results
                else (delegate_clean or "")
            ),
            meta={
                "background": bool(background_delegate),
                "delegate_used": list(delegate_used or []),
            },
        )

    ctx["_tool_results"] = tool_results
    if session_key:
        tool_trigger_store.remember(
            session_key, list(used_triggers or effective_triggers)
        )
        if used_triggers and any(_is_search_trigger(trigger) for trigger in used_triggers):
            hooks.remember_search_topic(
                followup_search_query or forced_search_query or user_text,
                ctx,
            )

    reasoning_text = hooks.clean_text_for_tts(
        hooks.strip_cmd_anywhere(hooks.strip_emo_tags_anywhere(clean_thought or reply1 or ""))
    ).strip()
    if reasoning_text:
        hooks.add_codex_session_event(
            "assistant_reasoning",
            text=reasoning_text,
            ctx=ctx,
            meta={"used_triggers": list(used_triggers or [])[:8]},
        )

    start_emo, start_intensity = hooks.reply_start_emotion(ctx)
    if triggered and tool_results and used_triggers:
        tool_use_meta = {"path": "tool_use"}
        if memory_session_id:
            tool_use_meta["session_id"] = memory_session_id
        asyncio.create_task(
            hooks.add_memory_safe(
                "assistant",
                f"[tool_use] {used_triggers}",
                meta=tool_use_meta,
            )
        )

    if triggered and tool_results:
        tool_finalize = await tool_flow_service.finalize_tool_reply(
            clean_thought=clean_thought,
            tool_results=tool_results,
            used_triggers=used_triggers,
            context_messages=context_messages,
            route_reason=str(getattr(route, "reason", "") or ""),
            task_default=task_default,
            start_emo=start_emo,
            chat_with_ai=hooks.plain_chat,
            extract_emo_tag=hooks.extract_emo_tag,
            character_sharing_enabled=character_sharing_enabled,
            try_share=hooks.try_share,
            is_model_error_reply=hooks.looks_like_upstream_error,
        )
        final_reply = tool_finalize.final_reply
        final_emo = tool_finalize.final_emo
        model_emo_seen = tool_finalize.model_emo_seen
    else:
        model_finalize = reply_flow_service.finalize_model_reply(
            reply=reply1,
            start_emo=start_emo,
            extract_emo_tag=hooks.extract_emo_tag,
            character_sharing_enabled=character_sharing_enabled,
            try_share=hooks.try_share,
        )
        final_reply = model_finalize.final_reply
        final_emo = model_finalize.final_emo
        model_emo_seen = model_finalize.model_emo_seen

    prepared_reply = await reply_flow_service.prepare_final_reply(
        final_reply=final_reply,
        final_emo=final_emo,
        model_emo_seen=model_emo_seen,
        user_text=user_text,
        ctx=ctx,
        preface_text=preface_text,
        clean_text_for_tts=hooks.clean_text_for_tts,
        strip_internal_tags=hooks.strip_internal_tags,
        strip_cmd_anywhere=hooks.strip_cmd_anywhere,
        strip_emo_tags_anywhere=hooks.strip_emo_tags_anywhere,
        should_suppress_followup_preface=hooks.should_suppress_followup_preface,
        merge_preface_texts=hooks.merge_preface_texts,
        apply_character_catchphrase=hooks.apply_character_catchphrase,
        prepare_reply_for_output=hooks.prepare_reply_for_output,
        infer_reply_emotion_with_llm=hooks.infer_reply_emotion_with_llm,
    )
    final_reply = prepared_reply.text
    final_emo = prepared_reply.emotion
    if not triggered:
        final_reply, guard_trace = await hooks.apply_forbidden_guard(
            text=final_reply,
            user_text=user_text,
            ctx=ctx,
        )
        if hooks.trace_process:
            try:
                if guard_trace.get("forbidden_phrase_hit") or guard_trace.get(
                    "forbidden_retries"
                ):
                    hooks.trace_process("forbidden_phrase_guard", **guard_trace)
            except Exception:
                pass

    return NonStreamTurnResult(
        reply=str(final_reply or ""),
        emotion=str(final_emo or "neutral"),
        triggered=bool(triggered),
        tool_event_state=tool_event_state,
        start_emo=start_emo,
        start_intensity=start_intensity,
        background_queued=background_queued,
    )
