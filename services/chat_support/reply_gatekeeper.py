"""Decide whether a message should get a model reply."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional, Sequence

from core.message_source import is_hud_user_source


async def should_reply(
    user_text: str,
    *,
    ctx: Optional[dict] = None,
    last_reply_time: float = 0.0,
    get_short_term_messages: Optional[Callable[..., list]] = None,
    is_searchworthy_question: Optional[Callable[[str], bool]] = None,
    chat_with_ai: Optional[Callable[..., Any]] = None,
    logger: Any = None,
    enabled: bool = False,
    wake_keywords: Sequence[str] = (),
    whitelist: Sequence[str] = (),
    blacklist: Sequence[str] = (),
    prompt_template: str = "",
    active_session_window: int = 20,
    now: Callable[[], float] = time.time,
) -> bool:
    text_clean = (user_text or "").strip()
    if len(text_clean) < 2:
        return False

    lower_text = text_clean.lower()
    wake_words = [str(word).lower() for word in (wake_keywords or []) if str(word).strip()]
    if any(word in lower_text for word in wake_words):
        if logger:
            logger.info("🟢 [Gatekeeper] 命中唤醒词 -> 强制回复")
        return True
    if callable(is_searchworthy_question) and is_searchworthy_question(text_clean):
        if logger:
            logger.info("🟢 [Gatekeeper] 明确问题 -> 强制回复")
        return True

    short_term_messages = []
    if callable(get_short_term_messages):
        try:
            short_term_messages = list(get_short_term_messages(ctx) or [])
        except Exception:
            short_term_messages = []
    source = str((ctx or {}).get("source") or "").strip().lower()
    if short_term_messages and not is_hud_user_source(source):
        last_msg = short_term_messages[-1]
        if last_msg.get("role") == "assistant":
            if logger:
                logger.info("🛑 [Gatekeeper] 上一条为 assistant -> 忽略")
            return False

    if not enabled:
        return True

    for word in blacklist or ():
        if word in text_clean:
            if logger:
                logger.info(f"🛑 [Gatekeeper] 命中黑名单 [{word}] -> 忽略")
            return False

    for word in whitelist or ():
        if str(word).lower() in text_clean.lower():
            if logger:
                logger.info(f"🟢 [Gatekeeper] 命中白名单 [{word}] -> 强制回复")
            return True

    time_diff = now() - float(last_reply_time or 0)
    window = int(active_session_window or 0)
    if window and time_diff < window:
        if logger:
            logger.info(
                f"🟢 [Gatekeeper] 处于活跃会话窗口 ({int(time_diff)}s < {window}s) -> 强制回复"
            )
        return True

    try:
        last_ai_reply = "无"
        for msg in reversed(short_term_messages):
            if msg.get("role") == "assistant":
                last_ai_reply = str(msg.get("content") or "")[:50]
                break
        prompt = str(prompt_template or "").format(
            user_text=text_clean, last_ai_reply=last_ai_reply
        )
        if not callable(chat_with_ai):
            return True
        decision = await asyncio.to_thread(
            chat_with_ai,
            [{"role": "user", "content": prompt}],
            task_type="gatekeeper",
            caller="chat_gatekeeper",
        )
        decision = str(decision or "").strip().upper()
        if logger:
            logger.info(
                f"⚖️ [Gatekeeper] LLM 判断结果: {decision} | 输入: {text_clean[:20]}..."
            )
        return "YES" in decision
    except Exception as exc:
        if logger:
            logger.error(f"⚠️ [Gatekeeper] 判断出错，默认放行: {exc}")
        return True
