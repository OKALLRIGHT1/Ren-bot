from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable, Optional

from modules.live2d import estimate_bubble_display_ms
from services.chat_support import sensor_utils, text_utils


class SensorReplyService:
    def __init__(
        self,
        *,
        event_bus: Any,
        presenter: Any,
        logger: Any,
        extract_emo_tag: Callable[[str], tuple[Optional[str], str]],
        strip_wrapping_quotes: Callable[[str], str],
        polish_natural_reply: Callable[..., Awaitable[str]],
        apply_character_catchphrase: Callable[[str], str],
        prepare_reply_for_output: Callable[..., str],
        looks_like_sensor_template_reply: Callable[[str], bool],
        remember_sensor_reply: Callable[[str], None],
        update_active_time: Callable[[], None],
        infer_reply_emotion_with_llm: Callable[..., Awaitable[Optional[str]]],
        get_current_live2d_emotion: Callable[[], tuple[str, float]],
        add_memory_safe: Callable[..., Awaitable[None]],
        last_reply_time_getter: Callable[[], float],
        conversation_event_service: Any = None,
        rescue_sensor_template_reply: Optional[Callable[..., Awaitable[str]]] = None,
        reset_sensor_motion_after: Optional[Callable[..., Awaitable[None]]] = None,
        clean_text_for_tts: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.event_bus = event_bus
        self.presenter = presenter
        self.logger = logger
        self.extract_emo_tag = extract_emo_tag
        self.strip_wrapping_quotes = strip_wrapping_quotes
        self.polish_natural_reply = polish_natural_reply
        self.apply_character_catchphrase = apply_character_catchphrase
        self.prepare_reply_for_output = prepare_reply_for_output
        self.looks_like_sensor_template_reply = looks_like_sensor_template_reply
        self.remember_sensor_reply = remember_sensor_reply
        self.update_active_time = update_active_time
        self.infer_reply_emotion_with_llm = infer_reply_emotion_with_llm
        self.get_current_live2d_emotion = get_current_live2d_emotion
        self.add_memory_safe = add_memory_safe
        self.last_reply_time_getter = last_reply_time_getter
        self.conversation_event_service = conversation_event_service
        self.clean_text_for_tts = clean_text_for_tts or text_utils.clean_text_for_tts
        self.rescue_sensor_template_reply = (
            rescue_sensor_template_reply or self.rescue_template_reply
        )
        self.reset_sensor_motion_after = (
            reset_sensor_motion_after or self.reset_motion_after
        )

    async def reset_motion_after(
        self, delay_s: float, *, reply_started_at: float
    ) -> None:
        try:
            await asyncio.sleep(max(0.2, float(delay_s)))
            if float(self.last_reply_time_getter() or 0) > float(reply_started_at) + 1e-6:
                return
            await self.event_bus.emit("live2d.go_idle")
        except Exception:
            return

    async def rescue_template_reply(
        self, text: str, *, title: str, category: str
    ) -> str:
        clean = str(self.clean_text_for_tts(str(text or "")) or "").strip()
        if not clean:
            return ""
        try:
            from modules.llm import chat_with_ai

            prompt = (
                "把下面这句屏幕感知回应改成一句自然的临场短话。\n"
                "要求：直接对 Master 说；不要像观察报告；不要出现“用户正在、屏幕上、画面中、我看到、根据、当前窗口、"
                "挺实用、步骤详尽、需要协助、这也要看”。\n"
                "允许关心、疑问、陪一句或很轻的吐槽；最多 28 个字；只输出最终一句。\n"
                f"窗口：{title} / {category}\n"
                f"原句：{clean}"
            )
            reply = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "user", "content": prompt}],
                task_type="default",
                caller="sensor_template_rescue",
            )
            rescued = str(
                self.clean_text_for_tts(
                    self.strip_wrapping_quotes(
                        text_utils.strip_internal_tags(str(reply or ""))
                    )
                )
                or ""
            ).strip()
            if rescued and not sensor_utils.looks_like_sensor_template_reply(
                rescued, clean_text_fn=self.clean_text_for_tts
            ):
                return rescued
        except Exception:
            pass
        return ""

    async def send_sensor_reply(
        self,
        reply: str,
        category: str,
        count: int,
        title: str,
        is_vision: bool,
        *,
        observation_event_id: str = "",
        ctx: Optional[dict] = None,
    ) -> bool:
        extracted_emo, clean_text = self.extract_emo_tag(reply)
        clean_text = self.strip_wrapping_quotes(clean_text)
        original_clean_text = clean_text

        lowered = str(clean_text or "").lower()
        bad_patterns = [
            "we need to",
            "your task",
            "up to 20 characters",
            "up to 36 characters",
            "直接对他说话进行吐槽",
            "结合用户屏幕上的主要内容",
        ]
        if any(pattern in lowered for pattern in bad_patterns):
            self.logger.warning("⚠️ [Sensor] 视觉吐槽输出疑似复述提示词，已丢弃")
            return False

        if not clean_text or len(clean_text) < 2:
            return False
        clean_text = await self.polish_natural_reply(
            user_text=f"{title} {category}",
            draft_text=clean_text,
            ctx={"source": "desktop"},
            scene="sensor",
        )
        clean_text = self.apply_character_catchphrase(clean_text)
        post_emo, post_clean = self.extract_emo_tag(clean_text)
        if post_emo:
            extracted_emo = post_emo
            clean_text = post_clean
        clean_text = self.prepare_reply_for_output(
            clean_text, {"source": "desktop"}, scene="sensor"
        )
        if not clean_text:
            return False

        # 规则护栏：禁止把会话次数念成「打开了 N 次」（不另开 polish/rescue LLM）
        sanitized, open_count_hits = sensor_utils.sanitize_sensor_open_count_reply(
            clean_text
        )
        if open_count_hits:
            if not sanitized:
                preview = re.sub(r"\s+", " ", clean_text)[:80]
                self.logger.warning(
                    "⚠️ [Sensor] 吐槽含「打开了N次」类夸张且改写后为空，已跳过: "
                    f"hits={open_count_hits} text={preview}"
                )
                return False
            self.logger.info(
                f"🧹 [Sensor] 已去掉打开次数夸张表述: hits={open_count_hits}"
            )
            clean_text = sanitized

        if self.looks_like_sensor_template_reply(clean_text):
            fallback_text = self.prepare_reply_for_output(
                original_clean_text, {"source": "desktop"}, scene="sensor"
            )
            if fallback_text:
                fb_sanitized, _ = sensor_utils.sanitize_sensor_open_count_reply(
                    fallback_text
                )
                fallback_text = fb_sanitized or fallback_text
            if (
                fallback_text
                and fallback_text != clean_text
                and not self.looks_like_sensor_template_reply(fallback_text)
            ):
                clean_text = fallback_text
            else:
                rescued = await self.rescue_sensor_template_reply(
                    clean_text, title=title, category=category
                )
                if rescued:
                    rescued_clean, _ = sensor_utils.sanitize_sensor_open_count_reply(
                        rescued
                    )
                    clean_text = rescued_clean or rescued
                else:
                    preview = re.sub(r"\s+", " ", clean_text)[:80]
                    self.logger.warning(
                        f"⚠️ [Sensor] 吐槽仍像观察报告/助手话术，已跳过本次输出: {preview}"
                    )
                    return False

        self.logger.info(f"🤖 [Sensor] 发言: {clean_text[:50]}...")
        self.remember_sensor_reply(clean_text)
        self.update_active_time()

        if not extracted_emo:
            extracted_emo = await self.infer_reply_emotion_with_llm(
                clean_text, scene="sensor"
            )

        current_emo, current_intensity = self.get_current_live2d_emotion()
        final_emo = extracted_emo or current_emo
        emo_intensity = (
            sensor_utils.sensor_emotion_intensity(final_emo)
            if extracted_emo
            else current_intensity
        )

        await self.event_bus.emit(
            "live2d.emotion",
            emotion=final_emo,
            intensity=emo_intensity,
            prefer_motion=False,
            reason="sensor_reply",
        )

        await self.presenter.present(
            clean_text, emotion=final_emo, interrupt=False, append_ui=True
        )

        delay = max(
            3.2, min(8.5, estimate_bubble_display_ms(clean_text) / 1000.0 + 0.35)
        )
        asyncio.create_task(
            self.reset_sensor_motion_after(
                delay,
                reply_started_at=self.last_reply_time_getter(),
            )
        )

        tag = "[视觉观察]" if is_vision else "[屏幕观察]"
        # T1 dual-write: transcript for UI/search; events = near-history authority.
        await self.add_memory_safe(
            "assistant",
            f"{tag} {clean_text}",
            meta={"path": "sensor", "emotion": final_emo},
        )
        event_service = self.conversation_event_service
        if event_service is not None and getattr(event_service, "is_ready", False):
            event_ctx = dict(ctx or {"source": "desktop"})
            if not event_ctx.get("source"):
                event_ctx["source"] = "desktop"
            # Causality must come from this generation's observation id only.
            # Do not fall back to any shared/last observation field.
            parent_id = str(observation_event_id or "").strip()
            try:
                event_service.record_proactive_utterance(
                    ctx=event_ctx,
                    text=clean_text,
                    parent_event_id=parent_id,
                    metadata={
                        "path": "sensor",
                        "emotion": final_emo,
                        "is_vision": bool(is_vision),
                        "category": str(category or ""),
                        "title": str(title or ""),
                    },
                )
            except Exception as exc:
                if self.logger:
                    self.logger.warning(
                        f"[Sensor] proactive utterance event failed: {exc}"
                    )
        return True
