from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional


class EmotionReplyService:
    def __init__(
        self,
        *,
        app_getter: Callable[[], Any],
        clean_text_for_tts: Callable[[str], str],
        strip_emo_tags: Callable[[str], str],
        strip_cmd: Callable[[str], str],
        normalize_emo: Callable[[Any], Optional[str]],
        personality_state_getter: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        logger: Any = None,
    ) -> None:
        self.app_getter = app_getter
        self.clean_text_for_tts = clean_text_for_tts
        self.strip_emo_tags = strip_emo_tags
        self.strip_cmd = strip_cmd
        self.normalize_emo = normalize_emo
        self.personality_state_getter = personality_state_getter
        self.logger = logger

    @staticmethod
    def _clamp_intensity(value: Any, default: float = 0.3) -> float:
        try:
            intensity = float(value)
        except Exception:
            intensity = default
        return max(0.0, min(1.0, intensity))

    def get_current_live2d_emotion(self) -> tuple[str, float]:
        app = self.app_getter()
        emo_ctrl = getattr(app, "emotion_controller", None) if app is not None else None
        if emo_ctrl is None:
            return "neutral", 0.3
        label = str(getattr(emo_ctrl, "current_emotion", "") or "neutral").strip().lower()
        if not label:
            label = "neutral"
        return label, self._clamp_intensity(getattr(emo_ctrl, "current_intensity", 0.3))

    def reply_start_emotion(self, ctx: Optional[dict[str, Any]] = None) -> tuple[str, float]:
        if isinstance(ctx, dict):
            stored = ctx.get("_reply_start_emotion")
            if isinstance(stored, (list, tuple)) and len(stored) >= 2:
                label = str(stored[0] or "neutral").strip().lower() or "neutral"
                return label, self._clamp_intensity(stored[1])
        return self.get_current_live2d_emotion()

    def build_current_emotion_context(
        self, ctx: Optional[dict[str, Any]] = None
    ) -> str:
        label, intensity = self.reply_start_emotion(ctx)
        lines = [
            "【当前Live2D情绪状态】",
            f"- 当前表情/情绪：{label}，强度约 {intensity:.2f}",
        ]
        mood_context = self._build_personality_state_context()
        if mood_context:
            lines.extend(mood_context)
        lines.extend(
            [
                "- 把这当作上一秒的状态：可以延续、变淡，也可以按语境改。",
                "- 不要因为旧表情机械保持，也不要无理由固定 neutral。",
            ]
        )
        return "\n".join(lines)

    def _build_personality_state_context(self) -> list[str]:
        if self.personality_state_getter is None:
            return []
        try:
            state = self.personality_state_getter() or {}
        except Exception as exc:
            if self.logger is not None:
                self.logger.debug(f"获取内在状态失败: {exc}")
            return []
        if not isinstance(state, dict):
            return []

        mood = str(state.get("mood") or "normal").strip() or "normal"
        energy = state.get("energy", "?")
        social_mode = str(state.get("social_mode") or "casual").strip() or "casual"
        continuity = str(state.get("continuity_emotion") or "neutral").strip() or "neutral"
        return [
            f"【内在状态】mood={mood}, energy={energy}, social={social_mode}; continuity={continuity}",
            "- 措辞参考：tired→更短更低能量；good→轻快但不过度；concerned→多关心少展开。",
        ]

    async def infer_reply_emotion_with_llm(
        self, text: str, *, scene: str = "chat"
    ) -> Optional[str]:
        clean = self.clean_text_for_tts(
            self.strip_cmd(self.strip_emo_tags(text or ""))
        ).strip()
        if not clean:
            return None
        try:
            from modules.llm import chat_with_ai

            reply = await asyncio.to_thread(
                chat_with_ai,
                [
                    {
                        "role": "user",
                        "content": (
                            "只判断下面这句适合哪个Live2D情绪标签。\n"
                            "只能输出一个英文标签，不要解释："
                            "happy/sad/angry/shy/flustered/confused/think/neutral\n"
                            f"场景：{scene}\n"
                            f"句子：{clean[:300]}"
                        ),
                    }
                ],
                task_type="reply_polish",
                caller="reply_emotion_fallback",
            )
            return self.normalize_emo(str(reply or "").strip())
        except Exception as exc:
            if self.logger is not None:
                self.logger.debug(f"情绪兜底判断失败: {exc}")
        return None
