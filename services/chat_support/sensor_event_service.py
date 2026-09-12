from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from modules.sensor_prompts import format_sensor_prompt, join_prompt_parts
from services.chat_support.emotion_reply_service import live2d_emo_tag_choices
from services.chat_support.sensor_event_guard import (
    check_sensor_event_guard,
    revalidate_focus_for_sensor,
)


@dataclass(frozen=True)
class SensorGenerationContext:
    context_block: str
    sensor_persona_prompt: str
    recent_sensor_reply_block: str
    text_style_block: str
    vision_style_block: str
    record_observation: Callable[[str, str], str]


@dataclass(frozen=True)
class SensorGatekeeperResult:
    allowed: bool
    reason: str = ""
    decision: str = ""


@dataclass(frozen=True)
class SensorReplyGenerationResult:
    reply: str = ""
    reason: str = ""
    branch: str = ""
    observation_event_id: str = ""


class SensorEventService:
    """Prepares screen-sensor generation context without owning locks or output."""

    def __init__(
        self,
        *,
        screen_sensor_ref_getter: Callable[[], Any],
        format_sensor_observations: Callable[..., str],
        build_sensor_usage_context: Callable[..., str],
        build_sensor_interaction_context: Callable[[], str],
        build_sensor_persona_prompt: Callable[..., str],
        format_recent_sensor_reply_block: Callable[[], str],
        build_sensor_spontaneous_style_block: Callable[..., str],
        build_live2d_self_awareness_hint: Callable[..., str],
        compress_sensor_text: Callable[..., str],
        logger: Any = None,
        conversation_event_service: Any = None,
    ) -> None:
        self.screen_sensor_ref_getter = screen_sensor_ref_getter
        self.format_sensor_observations = format_sensor_observations
        self.build_sensor_usage_context = build_sensor_usage_context
        self.build_sensor_interaction_context = build_sensor_interaction_context
        self.build_sensor_persona_prompt = build_sensor_persona_prompt
        self.format_recent_sensor_reply_block = format_recent_sensor_reply_block
        self.build_sensor_spontaneous_style_block = build_sensor_spontaneous_style_block
        self.build_live2d_self_awareness_hint = build_live2d_self_awareness_hint
        self.compress_sensor_text = compress_sensor_text
        self.logger = logger
        self.conversation_event_service = conversation_event_service
        # (display_app -> (monotonic_ts, last_rejected)) —— gatekeeper 同应用
        # 冷却表，避免同一窗口几分钟内反复问 LLM（答案几乎全是 NO）。
        self._gatekeeper_cache: dict[str, tuple[float, bool]] = {}
        self._gatekeeper_cooldown_sec = 30 * 60

    def _active_window_title(self) -> str:
        sensor_ref = self.screen_sensor_ref_getter()
        rust_title = str(getattr(sensor_ref, "last_window_title", "") or "").strip()
        try:
            from modules.vision.capture import get_active_window_title

            captured = str(get_active_window_title() or "").strip()
        except Exception:
            captured = ""
        return captured or rust_title

    async def handle_window_event(
        self,
        *,
        window_title: str,
        category: str,
        count: int = 1,
        use_vision: bool = False,
        app_name: str = "",
        reason: str = "",
        app_duration_sec: float | int | None = None,
        current_stay_sec: float | int | None = None,
        last_reply_time: float = 0.0,
        min_reply_interval_sec: float = 45.0,
        vision_mode: str = "separate",
        chat_with_ai: Callable[..., str],
        analyze_image: Callable[..., Any],
        send_sensor_reply: Callable[..., Awaitable[bool]],
    ) -> bool:
        guard = check_sensor_event_guard(
            window_title=window_title,
            category=category,
            app_name=app_name,
            last_reply_time=last_reply_time,
            min_reply_interval_sec=min_reply_interval_sec,
        )
        clean_title = guard.clean_title
        display_app = guard.display_app
        if guard.reason == "system_window":
            self._log_info(f"🛑 [Sensor] 跳过系统/自身界面视觉吐槽 ({clean_title})")
            return False
        if not guard.allowed:
            return False

        print(
            f"🤖 [Sensor] 观察: {clean_title} ({category}) | App: {display_app} | Count: {count} | Vision: {bool(use_vision)}"
        )
        generation = await self.run_event_generation(
            clean_title=clean_title,
            display_app=display_app,
            category=category,
            count=count,
            reason=reason,
            use_vision=bool(use_vision),
            vision_mode=vision_mode,
            app_duration_sec=app_duration_sec,
            current_stay_sec=current_stay_sec,
            chat_with_ai=chat_with_ai,
            analyze_image=analyze_image,
            active_title_getter=self._active_window_title,
        )
        if not generation.reply:
            return False
        reply_category = "self" if generation.branch == "self" else category
        reply_title = clean_title if generation.branch == "self" else window_title
        is_vision = generation.branch.startswith("vision")
        return await send_sensor_reply(
            generation.reply,
            reply_category,
            count,
            reply_title,
            is_vision,
            observation_event_id=str(generation.observation_event_id or ""),
            ctx={"source": "desktop"},
        )

    def build_generation_context(
        self,
        *,
        clean_title: str,
        display_app: str,
        category: str,
        count: int,
        reason: str,
        app_duration_sec: float | int | None = None,
        current_stay_sec: float | int | None = None,
    ) -> SensorGenerationContext:
        recent_observation_context = self._recent_observation_context()
        sensor_context_parts: list[str] = []
        usage_context = self.build_sensor_usage_context(
            app_name=display_app,
            category=category,
            count=count,
            reason=reason,
            app_duration_sec=app_duration_sec,
            current_stay_sec=current_stay_sec,
        )
        sensor_context_parts.append(usage_context)
        if recent_observation_context:
            sensor_context_parts.append(f"【近期观察】\n{recent_observation_context}")
        interaction_context = self.build_sensor_interaction_context()
        if interaction_context:
            sensor_context_parts.append(interaction_context)
        context_block = (
            "\n" + "\n\n".join(sensor_context_parts) + "\n"
            if sensor_context_parts
            else ""
        )

        return SensorGenerationContext(
            context_block=context_block,
            sensor_persona_prompt=self.build_sensor_persona_prompt(
                ctx={"source": "desktop", "sensor_category": category},
                extra_context=context_block,
            ),
            recent_sensor_reply_block=self.format_recent_sensor_reply_block(),
            text_style_block=self.build_sensor_spontaneous_style_block(
                title=clean_title, category=category, count=count, is_vision=False
            ),
            vision_style_block=self.build_sensor_spontaneous_style_block(
                title=clean_title, category=category, count=count, is_vision=True
            ),
            record_observation=lambda content, source: self.record_observation(
                content=content,
                source=source,
                clean_title=clean_title,
                category=category,
                display_app=display_app,
                reason=reason,
            ),
        )

    def _recent_observation_context(self) -> str:
        sensor_ref = self.screen_sensor_ref_getter()
        if sensor_ref is None:
            return ""
        try:
            recent_entries = sensor_ref.get_recent_observations(3)
            return self.format_sensor_observations(recent_entries, max_items=3)
        except Exception:
            return ""

    def record_observation(
        self,
        *,
        content: str,
        source: str,
        clean_title: str,
        category: str,
        display_app: str,
        reason: str,
        ctx: Optional[dict] = None,
    ) -> str:
        """Record screen observation; return conversation event id when available."""
        sensor_ref = self.screen_sensor_ref_getter()
        if sensor_ref is not None:
            add_fn = getattr(sensor_ref, "add_observation", None) or getattr(
                sensor_ref, "_append_observation", None
            )
            if add_fn:
                try:
                    add_fn(
                        content,
                        clean_title,
                        category,
                        app_name=display_app,
                        reason=reason,
                        source=source,
                    )
                except TypeError:
                    try:
                        add_fn(
                            content, clean_title, category, display_app, reason, source
                        )
                    except Exception:
                        pass
                except Exception:
                    pass

        evidence = str(content or "").strip()
        if not evidence:
            return ""
        event_id = ""
        event_service = self.conversation_event_service
        if event_service is not None and getattr(event_service, "is_ready", False):
            event_ctx = dict(ctx or {"source": "desktop"})
            if not event_ctx.get("source"):
                event_ctx["source"] = "desktop"
            try:
                event = event_service.record_screen_observation(
                    ctx=event_ctx,
                    evidence_summary=evidence,
                    exact_text=evidence,
                    metadata={
                        "source": str(source or ""),
                        "title": str(clean_title or ""),
                        "category": str(category or ""),
                        "app": str(display_app or ""),
                        "reason": str(reason or ""),
                    },
                )
                if event is not None:
                    event_id = str(event.event_id or "")
            except Exception as exc:
                if self.logger:
                    self.logger.warning(
                        f"[Sensor] conversation observation event failed: {exc}"
                    )
        return event_id

    def _talk_rules(self, *, max_chars: int = 36) -> str:
        return format_sensor_prompt(
            "talk_rules",
            max_chars=max_chars,
            emo_tags=live2d_emo_tag_choices(),
        )

    def build_self_prompt(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
    ) -> str:
        return join_prompt_parts(
            context.sensor_persona_prompt,
            format_sensor_prompt("self_scene", clean_title=clean_title),
            self._talk_rules(max_chars=15),
            context.recent_sensor_reply_block,
        )

    async def run_self_generation(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        count: int,
        chat_with_ai: Callable[..., str],
    ) -> SensorReplyGenerationResult:
        if count > 1 and random.random() > 0.7:
            return SensorReplyGenerationResult(reason="random_skip", branch="self")
        prompt = self.build_self_prompt(context=context, clean_title=clean_title)
        try:
            reply = await asyncio.to_thread(
                chat_with_ai,
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请说一句符合上述要求的话。"},
                ],
                task_type="default",
                caller="sensor_self_talk",
            )
            return SensorReplyGenerationResult(
                reply=str(reply or "").strip(),
                reason="generated" if str(reply or "").strip() else "empty",
                branch="self",
            )
        except Exception:
            return SensorReplyGenerationResult(reason="failed", branch="self")

    def build_gatekeeper_prompt(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        category: str,
        count: int,
    ) -> str:
        return format_sensor_prompt(
            "gatekeeper",
            context_block=context.context_block,
            clean_title=clean_title,
            category=category,
            count=count,
        )

    async def run_gatekeeper(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        display_app: str,
        category: str,
        count: int,
        chat_with_ai: Callable[..., str],
    ) -> SensorGatekeeperResult:
        # 只挡第一次看到的普通窗口；第二次及以后交给模型判断，避免整天几乎不说话。
        if count <= 1 and category not in {"self", "work", "coding"}:
            self._log_info(f"🛑 [Sensor Gatekeeper] 低强度事件跳过 ({clean_title})")
            return SensorGatekeeperResult(allowed=False, reason="low_intensity")

        # 同一应用冷却期内不重复问 LLM：切屏来回在几分钟内触发多次，
        # 每次 gatekeeper 都是一整轮 LLM 调用，实测几乎全是 NO。
        cache_key = str(display_app or clean_title or category or "?")
        now = time.monotonic()
        cached = self._gatekeeper_cache.get(cache_key)
        if cached is not None and now - cached[0] < self._gatekeeper_cooldown_sec:
            self._log_info(f"🛑 [Sensor Gatekeeper] 冷却中沿用上次拒绝 ({cache_key})")
            return SensorGatekeeperResult(allowed=False, reason="gatekeeper_cooldown")

        prompt = self.build_gatekeeper_prompt(
            context=context,
            clean_title=clean_title,
            category=category,
            count=count,
        )
        try:
            decision = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "user", "content": prompt}],
                task_type="gatekeeper",
                caller="sensor_gatekeeper",
            )
            decision_text = str(decision or "").strip()
            self._log_info(
                f"⚖️ [Sensor Gatekeeper] 判断是否值得吐槽: {decision_text}"
            )
            if "YES" not in decision_text.upper():
                self._log_info(
                    f"🛑 [Sensor Gatekeeper] 拦截本次纯文本回应 ({clean_title})，保持高冷"
                )
                self._gatekeeper_cache[cache_key] = (now, True)
                return SensorGatekeeperResult(
                    allowed=False,
                    reason="gatekeeper_rejected",
                    decision=decision_text,
                )
            self._gatekeeper_cache.pop(cache_key, None)
            return SensorGatekeeperResult(
                allowed=True,
                reason="gatekeeper_allowed",
                decision=decision_text,
            )
        except Exception as exc:
            self._log_warning(f"⚠️ [Sensor Gatekeeper] 调用失败，默认放行: {exc}")
            return SensorGatekeeperResult(allowed=True, reason="gatekeeper_failed")

    def _log_info(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger.info(message)
        except Exception:
            return

    def _log_warning(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger.warning(message)
        except Exception:
            return

    def _log_error(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger.error(message)
        except Exception:
            return

    def build_vision_direct_prompt(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
    ) -> str:
        return join_prompt_parts(
            context.sensor_persona_prompt,
            format_sensor_prompt("vision_direct_scene", clean_title=clean_title),
            self._talk_rules(),
            context.vision_style_block,
            context.recent_sensor_reply_block,
        )

    async def run_vision_direct_generation(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        image_base64: str,
        analyze_image: Optional[Callable[..., Any]],
    ) -> SensorReplyGenerationResult:
        prompt = self.build_vision_direct_prompt(
            context=context,
            clean_title=clean_title,
        )
        try:
            reply = await analyze_image(
                image_base64, prompt, caller="sensor_vision_direct"
            )
            observation_event_id = (
                context.record_observation(clean_title, "vision") if reply else ""
            )
            return SensorReplyGenerationResult(
                reply=str(reply or "").strip(),
                reason="generated" if str(reply or "").strip() else "empty",
                branch="vision_direct",
                observation_event_id=observation_event_id,
            )
        except Exception as exc:
            self._log_warning(f"Vision direct failed: {exc}")
            return SensorReplyGenerationResult(reason="failed", branch="vision_direct")

    def build_vision_description_prompt(self, *, self_awareness_hint: str) -> str:
        return format_sensor_prompt(
            "vision_describe",
            self_awareness_hint=self_awareness_hint or "",
        )

    def build_vision_talk_prompt(
        self,
        *,
        context: SensorGenerationContext,
        description: str,
    ) -> str:
        return join_prompt_parts(
            context.sensor_persona_prompt,
            format_sensor_prompt("vision_talk_scene", description=description),
            self._talk_rules(),
            context.vision_style_block,
            context.recent_sensor_reply_block,
        )

    async def run_vision_separate_generation(
        self,
        *,
        context: SensorGenerationContext,
        image_base64: str,
        analyze_image: Callable[..., Any],
        chat_with_ai: Callable[..., str],
    ) -> SensorReplyGenerationResult:
        try:
            self_awareness_hint = self.build_live2d_self_awareness_hint(
                {"source": "desktop"}
            )
            description_prompt = self.build_vision_description_prompt(
                self_awareness_hint=self_awareness_hint
            )
            description = await analyze_image(
                image_base64, description_prompt, caller="sensor_vision_describe"
            )
            if not description:
                return SensorReplyGenerationResult(
                    reason="empty_description",
                    branch="vision_separate",
                )

            description = self.compress_sensor_text(description, max_len=800)
            observation_event_id = context.record_observation(description, "vision")
            talk_prompt = self.build_vision_talk_prompt(
                context=context,
                description=description,
            )
            reply = await asyncio.to_thread(
                chat_with_ai,
                [
                    {
                        "role": "system",
                        "content": talk_prompt,
                    },
                    {
                        "role": "user",
                        "content": "请给出一句符合上述要求的临场回应。",
                    },
                ],
                task_type="sensor_vision_talk",
                caller="sensor_vision_talk",
            )
            return SensorReplyGenerationResult(
                reply=str(reply or "").strip(),
                reason="generated" if str(reply or "").strip() else "empty_reply",
                branch="vision_separate",
                observation_event_id=observation_event_id,
            )
        except Exception as exc:
            self._log_warning(f"Vision separate failed: {exc}")
            return SensorReplyGenerationResult(reason="failed", branch="vision_separate")

    def _rust_focus_titles(self) -> list[str]:
        sensor_ref = self.screen_sensor_ref_getter()
        if sensor_ref is None:
            return []
        titles: list[str] = []
        for attr in ("last_window_title", "last_app_name"):
            value = str(getattr(sensor_ref, attr, "") or "").strip()
            if value and value not in titles:
                titles.append(value)
        return titles

    def _sensor_vision_capture_target(self) -> str:
        try:
            import config

            target = str(
                getattr(config, "SENSOR_VISION_CAPTURE_TARGET", "active_monitor") or ""
            ).strip().lower()
        except Exception:
            target = "active_monitor"
        if target in {
            "primary",
            "active_monitor",
            "foreground_monitor",
            "focus_monitor",
            "active_window",
            "window",
            "all",
        }:
            return target
        return "active_monitor"

    async def run_vision_generation(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        vision_mode: str,
        analyze_image: Callable[..., Any],
        chat_with_ai: Callable[..., str],
        display_app: str = "",
        take_screenshot_base64: Optional[Callable[..., str]] = None,
        active_title_getter: Optional[Callable[[], str]] = None,
    ) -> SensorReplyGenerationResult:
        try:
            focus = revalidate_focus_for_sensor(
                event_title=clean_title,
                app_name=display_app,
                active_title_getter=active_title_getter,
                alternate_titles=self._rust_focus_titles(),
            )
            if not focus.ok:
                self._log_info(
                    f"🛑 [Sensor] 视觉采样前焦点已变，跳过: event={clean_title} active={focus.active_title}"
                )
                return SensorReplyGenerationResult(
                    reason="focus_mismatch",
                    branch="guard",
                )

            capture_target = self._sensor_vision_capture_target()
            if take_screenshot_base64 is None:
                from modules.vision.capture import (
                    take_screenshot_base64 as capture_screenshot_base64,
                )

                def _default_capture(max_size=1024, target=capture_target, monitor_index=1):
                    return capture_screenshot_base64(
                        max_size=max_size, target=target, monitor_index=monitor_index
                    )

                take_screenshot_base64 = _default_capture

            print(f"📸 [Sensor] 正在视觉采样... target={capture_target}")
            try:
                image_base64 = await asyncio.to_thread(
                    take_screenshot_base64, 1024, capture_target
                )
            except TypeError:
                # Backward-compatible fakes / older callables without target args.
                image_base64 = await asyncio.to_thread(take_screenshot_base64)
            if not image_base64:
                return SensorReplyGenerationResult(
                    reason="empty_screenshot",
                    branch="vision",
                )

            focus_after = revalidate_focus_for_sensor(
                event_title=clean_title,
                app_name=display_app,
                active_title_getter=active_title_getter,
                alternate_titles=self._rust_focus_titles(),
            )
            if not focus_after.ok:
                self._log_info(
                    f"🛑 [Sensor] 截图后焦点已变，丢弃画面: event={clean_title} active={focus_after.active_title}"
                )
                return SensorReplyGenerationResult(
                    reason="focus_mismatch",
                    branch="guard",
                )

            if vision_mode == "direct":
                return await self.run_vision_direct_generation(
                    context=context,
                    clean_title=clean_title,
                    image_base64=image_base64,
                    analyze_image=analyze_image,
                )

            if vision_mode == "separate":
                return await self.run_vision_separate_generation(
                    context=context,
                    image_base64=image_base64,
                    analyze_image=analyze_image,
                    chat_with_ai=chat_with_ai,
                )

            return SensorReplyGenerationResult(
                reason="unsupported_vision_mode",
                branch="vision",
            )
        except Exception as exc:
            self._log_warning(f"Vision failed: {exc}")
            return SensorReplyGenerationResult(reason="failed", branch="vision")

    def build_text_prompt(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        category: str,
        count: int,
        reason: str = "switch",
    ) -> str:
        scene_key = (
            "text_duration_scene"
            if str(reason or "").strip().lower() == "duration"
            else "text_switch_scene"
        )
        return join_prompt_parts(
            context.sensor_persona_prompt,
            format_sensor_prompt(
                scene_key,
                clean_title=clean_title,
                category=category,
                count=count,
            ),
            self._talk_rules(),
            context.text_style_block,
            context.recent_sensor_reply_block,
        )

    async def run_text_generation(
        self,
        *,
        context: SensorGenerationContext,
        clean_title: str,
        category: str,
        count: int,
        chat_with_ai: Callable[..., str],
        reason: str = "switch",
    ) -> SensorReplyGenerationResult:
        prompt = self.build_text_prompt(
            context=context,
            clean_title=clean_title,
            category=category,
            count=count,
            reason=reason,
        )
        try:
            observation_event_id = context.record_observation(clean_title, "text")
            reply = await asyncio.to_thread(
                chat_with_ai,
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请说一句符合上述要求的话。"},
                ],
                task_type="default",
                caller="sensor_text_talk",
            )
            return SensorReplyGenerationResult(
                reply=str(reply or "").strip(),
                reason="generated" if str(reply or "").strip() else "empty",
                branch="text",
                observation_event_id=observation_event_id,
            )
        except Exception as exc:
            self._log_error(f"Sensor Gen failed: {exc}")
            return SensorReplyGenerationResult(reason="failed", branch="text")

    async def run_event_generation(
        self,
        *,
        clean_title: str,
        display_app: str,
        category: str,
        count: int,
        reason: str,
        use_vision: bool = False,
        vision_mode: str = "separate",
        app_duration_sec: float | int | None,
        current_stay_sec: float | int | None,
        chat_with_ai: Callable[..., str],
        analyze_image: Optional[Callable[..., Any]] = None,
        active_title_getter: Optional[Callable[[], str]] = None,
        take_screenshot_base64: Optional[Callable[..., str]] = None,
    ) -> SensorReplyGenerationResult:
        # 非自身窗口：生成前先确认焦点还在事件窗口，避免串台。
        if category != "self":
            focus = revalidate_focus_for_sensor(
                event_title=clean_title,
                app_name=display_app,
                active_title_getter=active_title_getter,
                alternate_titles=self._rust_focus_titles(),
            )
            if not focus.ok:
                self._log_info(
                    f"🛑 [Sensor] 焦点已变，跳过吐槽: event={clean_title} active={focus.active_title}"
                )
                return SensorReplyGenerationResult(
                    reason="focus_mismatch",
                    branch="guard",
                )

        context = self.build_generation_context(
            clean_title=clean_title,
            display_app=display_app,
            category=category,
            count=count,
            reason=reason,
            app_duration_sec=app_duration_sec,
            current_stay_sec=current_stay_sec,
        )

        if category == "self":
            return await self.run_self_generation(
                context=context,
                clean_title=clean_title,
                count=count,
                chat_with_ai=chat_with_ai,
            )

        # 文本路径先过 gatekeeper；视觉路径直接看图，失败再回退文本。
        if not use_vision:
            gatekeeper_result = await self.run_gatekeeper(
                context=context,
                clean_title=clean_title,
                display_app=display_app,
                category=category,
                count=count,
                chat_with_ai=chat_with_ai,
            )
            if not gatekeeper_result.allowed:
                return SensorReplyGenerationResult(
                    reason=gatekeeper_result.reason or "gatekeeper_blocked",
                    branch="gatekeeper",
                )

        if use_vision:
            if analyze_image is None:
                self._log_warning("视觉路径缺少 analyze_image，回退文本生成")
            else:
                vision_generation = await self.run_vision_generation(
                    context=context,
                    clean_title=clean_title,
                    vision_mode=vision_mode,
                    analyze_image=analyze_image,
                    chat_with_ai=chat_with_ai,
                    display_app=display_app,
                    take_screenshot_base64=take_screenshot_base64,
                    active_title_getter=active_title_getter,
                )
                if vision_generation.reply:
                    return vision_generation
                if vision_generation.reason == "focus_mismatch":
                    return vision_generation
                self._log_info(
                    f"ℹ️ [Sensor] 视觉生成未产出回复，回退文本: reason={vision_generation.reason}"
                )

        return await self.run_text_generation(
            context=context,
            clean_title=clean_title,
            category=category,
            count=count,
            chat_with_ai=chat_with_ai,
            reason=reason,
        )


async def run_music_event(
    *,
    title: str,
    artist: str,
    last_reply_time: float,
    min_reply_interval_sec: float,
    event_bus: Any,
    presenter: Any,
    logger: Any,
    extract_emo_tag: Callable[[str], tuple[Optional[str], str]],
    apply_character_catchphrase: Callable[[str], str],
    observe_final_reply_emotion: Callable[[str], None],
    update_active_time: Callable[[], None],
    add_memory_safe: Callable[..., Awaitable[Any]],
    default_persona: str,
    chat_with_ai: Callable[..., str],
) -> None:
    if time.time() - float(last_reply_time or 0) < float(min_reply_interval_sec or 0):
        return

    print(f"🎵 [Music] 正在聆听: {title} by {artist}")
    asyncio.create_task(
        event_bus.emit("live2d.emotion", emotion="music", intensity=1.0)
    )
    base_prompt = default_persona
    try:
        from modules.character_manager import character_manager

        active_char = character_manager.get_active_character()
        if active_char:
            base_prompt = active_char.get("prompt", default_persona)
    except Exception:
        pass

    system_prompt = f"""
    {base_prompt}

    【当前场景】
    你正在陪用户一起听歌。歌曲信息：
    - 歌名：{title}
    - 歌手/作曲：{artist}

    【任务】
    请简短评价这首歌，或表达你的感受。

    【要求】
    - 用你自己的语气和方式评价，不要写成通用模板
    - 一句话即可（不超过30字）
    - 如果不熟悉这首歌，可以根据歌名/歌手风格推测
    """
    try:
        reply = await asyncio.to_thread(
            chat_with_ai,
            [{"role": "system", "content": system_prompt}],
            task_type="default",
            caller="music_event",
        )
        reply = (reply or "").strip()
        if not reply or len(reply) < 2:
            return
        extracted_emo, clean_text = extract_emo_tag(reply)
        clean_text = apply_character_catchphrase(clean_text)
        if not clean_text:
            return
        final_emo = extracted_emo or "neutral"
        if not extracted_emo:
            if "kajiura" in artist.lower() or "梶浦" in artist:
                final_emo = "think"
        observe_final_reply_emotion(final_emo)
        await presenter.present(
            clean_text, emotion=final_emo, interrupt=False, append_ui=True
        )
        update_active_time()
        asyncio.create_task(
            add_memory_safe(
                "assistant",
                f"我评价了歌曲《{title}》: {clean_text}",
                meta={"path": "music"},
            )
        )
    except Exception as exc:
        if logger:
            logger.error(f"处理音乐事件失败: {exc}")
