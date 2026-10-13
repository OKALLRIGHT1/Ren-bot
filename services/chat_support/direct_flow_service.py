"""Direct-command result interpretation and turn finalization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from services.chat_support.flow_result import (
    STATUS_HANDLED_SILENT,
    STATUS_REPLIED,
    ChatFlowResult,
)
from services.chat_support.gateway_sender import QQ_REMOTE_SOURCES
from services.chat_support.output_coordinator import (
    emit_assistant_ui_parts,
    should_pace_assistant_ui,
)


def _source(ctx: Optional[Dict[str, Any]]) -> str:
    return str((ctx or {}).get("source") or "").strip().lower()


def _is_qq_source(ctx: Optional[Dict[str, Any]]) -> bool:
    return _source(ctx) in QQ_REMOTE_SOURCES


def _result_type(direct_result: Any) -> str:
    if not isinstance(direct_result, dict):
        return ""
    return str(direct_result.get("__type__") or "").strip()


@dataclass
class DirectFlowHooks:
    send_text: Callable[..., Awaitable[Any]]
    send_image: Callable[..., Awaitable[bool]]
    send_file: Callable[..., Awaitable[bool]]
    send_voice: Callable[..., Awaitable[bool]]
    cleanup_image: Callable[..., Awaitable[Any]]
    present_direct_tool_text: Callable[..., Awaitable[str]]
    presenter: Any
    event_bus: Any
    add_memory_safe: Callable[..., Awaitable[Any]]
    update_active_time: Callable[[], None]
    set_codex_task_state: Callable[..., None]
    emit_idle_status_when_safe: Callable[..., Awaitable[Any]]
    schedule_app_restart: Callable[[Dict[str, Any]], None]
    logger: Any = None
    send_forward: Optional[Callable[..., Awaitable[bool]]] = None


@dataclass
class DirectTurnOutcome:
    flow: ChatFlowResult
    pending_restart: Optional[Dict[str, Any]] = None
    skip_gateway_text: bool = False


async def interpret_direct_result(
    *,
    user_text: str,
    direct_result: Any,
    ctx: Optional[Dict[str, Any]],
    hooks: DirectFlowHooks,
    source: str = "",
) -> DirectTurnOutcome:
    """Send Direct media and decide display vs memory text. Does not present or record."""
    reply_text = str(direct_result) if direct_result is not None else ""
    memory_text = reply_text
    skip_gateway_text = False
    pending_restart: Optional[Dict[str, Any]] = None
    result_type = _result_type(direct_result)

    if result_type == "app_restart":
        reply_text = str(
            direct_result.get("message") or "收到，正在重启主程序。"
        ).strip()
        memory_text = reply_text
        pending_restart = dict(direct_result)

    if result_type == "gateway_voice":
        voice_path = str(direct_result.get("voice_path") or "").strip()
        post_send_text = str(direct_result.get("post_send_text") or "").strip()
        success_text = str(
            direct_result.get("success_text") or "🔊 已把语音发给你了。"
        )
        fallback_text = str(
            direct_result.get("fallback_text") or "⚠️ 语音已准备好，但回发失败了。"
        )
        voice_ok = await hooks.send_voice(voice_path, ctx)
        reply_text = success_text if voice_ok else fallback_text
        memory_text = (post_send_text or success_text) if voice_ok else fallback_text
        skip_gateway_text = bool(voice_ok)
        if voice_ok:
            reply_text = ""
            if post_send_text and _is_qq_source(ctx):
                await hooks.send_text(post_send_text, ctx, emotion="neutral")

    if result_type == "gateway_file":
        file_path = str(direct_result.get("file_path") or "").strip()
        file_name = str(direct_result.get("file_name") or "").strip()
        success_text = str(
            direct_result.get("success_text") or "📎 已把文件发给你了。"
        )
        fallback_text = str(
            direct_result.get("fallback_text") or "⚠️ 文件已准备好，但回发失败了。"
        )
        file_ok = await hooks.send_file(file_path, ctx, file_name=file_name)
        reply_text = success_text if file_ok else fallback_text
        memory_text = reply_text
        skip_gateway_text = bool(file_ok)
        if not file_ok and _is_qq_source(ctx):
            await hooks.send_text(reply_text, ctx, emotion="neutral")

    if result_type == "gateway_image":
        image_path = str(direct_result.get("image_path") or "").strip()
        image_caption = str(direct_result.get("caption") or "")
        post_send_text = str(direct_result.get("post_send_text") or "").strip()
        send_caption_with_image = bool(direct_result.get("send_caption_with_image"))
        success_text = str(
            direct_result.get("success_text") or "🖼️ 已把当前截图发给你了。"
        )
        fallback_text = str(
            direct_result.get("fallback_text") or "⚠️ 截图已生成，但回发失败了。"
        )
        suppress_fallback_reply = bool(direct_result.get("suppress_fallback_reply"))
        image_ok = await hooks.send_image(
            image_path,
            ctx,
            caption=image_caption if send_caption_with_image else "",
        )
        if image_path and bool(direct_result.get("cleanup", True)):
            asyncio.create_task(hooks.cleanup_image(image_path))
        reply_text = "" if image_ok else fallback_text
        memory_text = (image_caption or success_text) if image_ok else fallback_text
        skip_gateway_text = bool(image_ok)
        if image_ok and post_send_text and _is_qq_source(ctx):
            await hooks.send_text(post_send_text, ctx, emotion="neutral")
        if (
            not image_ok
            and not suppress_fallback_reply
            and _is_qq_source(ctx)
        ):
            await hooks.send_text(reply_text, ctx, emotion="neutral")
            reply_text = ""
        elif not image_ok and suppress_fallback_reply:
            reply_text = ""

    if reply_text and not isinstance(direct_result, dict):
        reply_text = await hooks.present_direct_tool_text(
            user_text=user_text,
            direct_text=reply_text,
            ctx=ctx,
        )
        memory_text = reply_text

    status = STATUS_REPLIED if str(reply_text or "").strip() else STATUS_HANDLED_SILENT
    return DirectTurnOutcome(
        flow=ChatFlowResult(
            status=status,
            reply_text=str(reply_text or ""),
            memory_text=str(memory_text or ""),
            emotion="neutral",
            source=source or _source(ctx),
        ),
        pending_restart=pending_restart,
        skip_gateway_text=skip_gateway_text,
    )


async def run_direct_turn(
    *,
    user_text: str,
    direct_result: Any,
    ctx: Optional[Dict[str, Any]],
    output_profile: Dict[str, Any],
    transcript_channel_meta: Dict[str, Any],
    chat_log_source: str,
    memory_session_id: Optional[str],
    codex_mode: bool,
    hooks: DirectFlowHooks,
) -> ChatFlowResult:
    """Own the Direct turn: interpret, present, record, then restart if needed."""
    outcome = await interpret_direct_result(
        user_text=user_text,
        direct_result=direct_result,
        ctx=ctx,
        hooks=hooks,
        source=str((ctx or {}).get("source") or ""),
    )
    flow = outcome.flow
    reply_text = str(flow.reply_text or "")

    if reply_text:
        pace_ui = should_pace_assistant_ui(output_profile)
        if output_profile.get("ui_append", True) and not pace_ui:
            await emit_assistant_ui_parts(hooks.event_bus, reply_text)
        await hooks.presenter.present(
            reply_text,
            emotion="neutral",
            speak=output_profile.get("speak", True),
            show_bubble=output_profile.get("show_bubble", True),
            append_ui=pace_ui,
        )
        if not outcome.skip_gateway_text:
            await hooks.send_text(reply_text, ctx, emotion="neutral")
        hooks.update_active_time()
        if codex_mode:
            hooks.set_codex_task_state(ctx, "finalize", summary=reply_text[:200])

    if outcome.pending_restart is not None:
        hooks.schedule_app_restart(outcome.pending_restart)

    user_meta = {
        "path": "direct",
        "source": chat_log_source,
        **transcript_channel_meta,
    }
    assistant_meta = {
        "path": "direct",
        "source": chat_log_source,
        "tool": True,
        "emotion": "neutral",
        **transcript_channel_meta,
    }
    if memory_session_id:
        user_meta["session_id"] = memory_session_id
        assistant_meta["session_id"] = memory_session_id
    await hooks.event_bus.emit(
        "chat.log", role="user", content=user_text, meta=user_meta
    )
    await hooks.add_memory_safe("user", user_text, meta=user_meta)
    memory_reply = flow.text_for_memory().strip()
    if memory_reply:
        await hooks.event_bus.emit(
            "chat.log",
            role="assistant",
            content=memory_reply,
            meta=assistant_meta,
        )
        await hooks.add_memory_safe("assistant", memory_reply, meta=assistant_meta)

    await hooks.emit_idle_status_when_safe(
        output_profile,
        reason="direct_complete",
        had_presenter_output=bool(reply_text),
    )
    return flow
