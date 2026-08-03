from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from modules.memory_core import MemoryCoreService
from services.capability_manager import ToolCapability, ToolCapabilityMatch


class Plugin:
    name = "Memory Tools"
    type = "delegate"
    aliases = ["/memory", "/记忆"]
    direct_command_aliases = ["/memory", "/记忆"]
    description = "查询长期记忆、人物画像和本地活动统计。"
    example_arg = "query ||| 我上次开会开了多久"

    def get_capabilities(self):
        return [
            ToolCapability(
                id="memory.query",
                plugin="memory_tools",
                trigger_mode="natural",
                match=self._match_memory,
                description="查询用户过去说过的事实或共同经历",
                examples=["还记得我上次说了什么吗", "我之前提过的项目是什么"],
            ),
            ToolCapability(
                id="memory.person_profile",
                plugin="memory_tools",
                trigger_mode="natural",
                match=self._match_profile,
                description="查询当前人物画像和稳定偏好",
                examples=["你对我的印象是什么", "我喜欢什么"],
            ),
            ToolCapability(
                id="activity.query",
                plugin="memory_tools",
                trigger_mode="natural",
                match=self._match_activity,
                description="查询本地活动、学习、会议或软件使用时长",
                examples=["我昨天学习了多久", "我上次开会开了多久"],
            ),
        ]

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        core = self._get_core(ctx)
        if core is None:
            return "记忆服务当前不可用"
        raw = self._strip_alias(str(args or "").strip())
        action, query = self._parse_action(raw)
        query = query or str((ctx or {}).get("user_text") or "").strip()
        session_id = str((ctx or {}).get("memory_session_id") or "").strip()
        person_id = str((ctx or {}).get("memory_person_id") or "owner").strip() or "owner"

        if action == "profile" or core.detect_intent(query) == "profile":
            profile = await asyncio.to_thread(core.get_person_profile, person_id)
            return profile.text or "还没有形成可靠的人物画像"
        if action == "activity" or core.detect_intent(query) == "activity":
            return await asyncio.to_thread(core.query_activity, query)

        recent = list(getattr((ctx or {}).get("brain"), "short_term_memory", None) or [])[-8:]
        result = await asyncio.to_thread(
            core.build_reply_context,
            query,
            session_id=session_id,
            person_id=person_id,
            recent_messages=recent,
            use_llm=False,
        )
        return result.memory_text or "没有找到与这个问题直接相关的可靠记录"

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        return any(raw == alias or raw.startswith(alias + " ") for alias in self.direct_command_aliases)

    @staticmethod
    def _get_core(ctx: Dict[str, Any]) -> Optional[MemoryCoreService]:
        brain = (ctx or {}).get("brain")
        core = getattr(brain, "memory_core", None)
        return core if isinstance(core, MemoryCoreService) else None

    @staticmethod
    def _parse_action(text: str) -> tuple[str, str]:
        parts = [part.strip() for part in str(text or "").split("|||", 1)]
        if len(parts) == 2 and parts[0].lower() in {"query", "profile", "activity"}:
            return parts[0].lower(), parts[1]
        return "query", str(text or "").strip()

    def _strip_alias(self, text: str) -> str:
        for alias in self.direct_command_aliases:
            if text == alias:
                return ""
            if text.startswith(alias + " "):
                return text[len(alias) :].strip()
        return text

    @staticmethod
    def _match_memory(text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
        del ctx
        raw = str(text or "").strip()
        if MemoryCoreService.detect_intent(raw) != "episode":
            return None
        score = 0.9 if any(cue in raw for cue in ("还记得", "记得吗", "我上次", "我之前", "我说过")) else 0.66
        return ToolCapabilityMatch(
            capability_id="memory.query",
            plugin="memory_tools",
            score=score,
            raw_text=raw,
            reason="explicit_recall_intent",
        )

    @staticmethod
    def _match_profile(text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
        del ctx
        raw = str(text or "").strip()
        if MemoryCoreService.detect_intent(raw) != "profile":
            return None
        return ToolCapabilityMatch(
            capability_id="memory.person_profile",
            plugin="memory_tools",
            score=0.9,
            raw_text=raw,
            reason="explicit_profile_intent",
        )

    @staticmethod
    def _match_activity(text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
        del ctx
        raw = str(text or "").strip()
        if MemoryCoreService.detect_intent(raw) != "activity":
            return None
        return ToolCapabilityMatch(
            capability_id="activity.query",
            plugin="memory_tools",
            score=0.92,
            raw_text=raw,
            reason="explicit_activity_query",
        )
