from __future__ import annotations

import asyncio
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

    async def send_active_alert(self, app_name: str, minutes: int) -> None:
        print(f"⏰ [Chat] 收到久坐提醒请求: {app_name} ({minutes}m)")
        base_prompt = self._active_character_prompt()
        system_prompt = f"""
{base_prompt}

【当前情况】
用户已经连续使用电脑（键盘/鼠标一直有活动）{minutes} 分钟了，中间没有真正休息。
这是整机久坐，不是盯着某一个窗口。显示名「{app_name}」只表示电脑本身。

【任务】
请主动提醒他起来活动、喝水或休息一下。
用你自己的语气和方式提醒，不要写成通用模板，也不要说成某个软件用太久。
字数限制：30字以内。
"""
        try:
            from modules.llm import chat_with_ai

            reply = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "system", "content": system_prompt}],
                task_type="default",
                caller="active_alert",
            )
            if not reply:
                return
            extracted_emo, clean_reply = self.extract_emo_tag(reply)
            clean_reply = self.apply_character_catchphrase(clean_reply)
            if not clean_reply:
                return
            await self.presenter.present(
                f"【温馨提醒】{clean_reply}",
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
