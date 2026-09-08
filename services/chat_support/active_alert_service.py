from __future__ import annotations

import asyncio
import re
from typing import Any, Callable


class ActiveAlertService:
    def __init__(
        self,
        *,
        default_persona: str,
        event_bus: Any,
        presenter: Any,
        extract_emo_tag: Callable[[str], tuple[str | None, str]],
        apply_character_catchphrase: Callable[[str], str],
        logger: Any = None,
        conversation_event_service: Any = None,
    ) -> None:
        self.default_persona = default_persona
        self.event_bus = event_bus
        self.presenter = presenter
        self.extract_emo_tag = extract_emo_tag
        self.apply_character_catchphrase = apply_character_catchphrase
        self.logger = logger
        self.conversation_event_service = conversation_event_service

    def _active_character_prompt(self) -> str:
        try:
            from modules.character_manager import character_manager

            character = character_manager.get_active_character()
            if character:
                return character.get("prompt", self.default_persona)
        except Exception:
            pass
        return self.default_persona

    def _fallback_alert_text(self, minutes: int) -> str:
        mins = max(1, int(minutes or 0))
        return f"已经工作了 {mins} 分钟了，起来走走喝点水吧。"

    def _looks_like_prompt_leak(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return True
        markers = (
            "请把下面这句",
            "只输出最终提醒句",
            "不要复述",
            "【草稿】",
            "【任务】",
            "【当前情况】",
            "【温馨提醒】",
            "字数限制",
            "久坐提醒改成",
            "最多 28 字",
            "最多28字",
            "system_prompt",
            "role: system",
        )
        hit = sum(1 for marker in markers if marker in raw)
        if hit >= 2:
            return True
        if "请把下面这句" in raw or "【草稿】" in raw:
            return True
        if raw.startswith("请把") and ("改成" in raw or "输出" in raw):
            return True
        return False

    def _sanitize_alert_reply(self, text: str, *, minutes: int) -> str:
        raw = str(text or "").strip()
        if not raw:
            return self._fallback_alert_text(minutes)

        # 去掉明显指令块，尽量抢救正文。
        cleaned = re.sub(
            r"(?is)^\s*请把下面这句.*?只输出最终提醒句[。.!！]?\s*",
            "",
            raw,
        )
        cleaned = re.sub(r"(?is)^\s*【温馨提醒】\s*", "", cleaned)
        cleaned = re.sub(r"(?is)^\s*【草稿】\s*", "", cleaned)
        cleaned = re.sub(r"(?is)^\s*【任务】[\s\S]*$", "", cleaned)
        cleaned = re.sub(r"(?is)^\s*【当前情况】[\s\S]*$", "", cleaned)
        cleaned = cleaned.strip(" \n\r\t\"'“”")

        if not cleaned or self._looks_like_prompt_leak(cleaned):
            return self._fallback_alert_text(minutes)

        # 过长时截到一句短提醒，避免把整段提示词读出去。
        if len(cleaned) > 40:
            for sep in ("。", "！", "？", "!", "?", "\n"):
                if sep in cleaned:
                    head = cleaned.split(sep, 1)[0].strip()
                    if 4 <= len(head) <= 40 and not self._looks_like_prompt_leak(head):
                        cleaned = head
                        break
            if len(cleaned) > 40:
                return self._fallback_alert_text(minutes)
        return cleaned

    async def send_active_alert(self, app_name: str, minutes: int) -> None:
        print(f"⏰ [Chat] 收到久坐提醒请求: {app_name} ({minutes}m)")
        fallback = self._fallback_alert_text(minutes)
        persona = str(self._active_character_prompt() or "").strip()
        minutes_text = str(max(1, int(minutes or 0)))
        messages = [
            {
                "role": "system",
                "content": persona or "用当前角色的口吻说话。",
            },
            {
                "role": "user",
                "content": (
                    f"对方已经连续工作了 {minutes_text} 分钟。"
                    "用你自己的方式提醒对方起来活动一下。"
                    "只说一句，不要加标题。"
                ),
            },
        ]
        try:
            from modules.llm import chat_with_ai

            reply = await asyncio.to_thread(
                chat_with_ai,
                messages,
                task_type="default",
                caller="active_alert",
            )
            extracted_emo, clean_reply = self.extract_emo_tag(reply or "")
            clean_reply = self._sanitize_alert_reply(clean_reply, minutes=minutes)
            clean_reply = self.apply_character_catchphrase(clean_reply)
            if not clean_reply or self._looks_like_prompt_leak(clean_reply):
                clean_reply = fallback
                extracted_emo = "concern"
            await self.presenter.present(
                clean_reply,
                emotion=extracted_emo or "concern",
                interrupt=True,
                append_ui=True,
            )
            event_service = self.conversation_event_service
            if event_service is not None and getattr(event_service, "is_ready", False):
                try:
                    event_service.record_care_reminder(
                        ctx={"source": "desktop"},
                        text=clean_reply,
                        reason=f"sedentary:{app_name}:{minutes}m",
                        metadata={
                            "path": "active_alert",
                            "app_name": str(app_name or ""),
                            "minutes": int(minutes or 0),
                        },
                    )
                except Exception as event_exc:
                    if self.logger:
                        self.logger.warning(
                            f"[ActiveAlert] care event failed: {event_exc}"
                        )
        except Exception as exc:
            if self.logger:
                self.logger.error(f"Active alert failed: {exc}")
            try:
                await self.presenter.present(
                    fallback,
                    emotion="concern",
                    interrupt=True,
                    append_ui=True,
                )
            except Exception:
                pass
