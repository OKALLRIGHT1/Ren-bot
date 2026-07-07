from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, Optional

from core.message_source import build_output_profile
from services.chat_support import diary_utils


class DiaryService:
    def __init__(
        self,
        *,
        brain: Any,
        event_bus: Any,
        presenter: Any,
        logger: Any,
        add_memory_safe: Callable[..., Awaitable[None]],
        emit_idle_status_when_safe: Callable[..., Awaitable[None]],
        send_gateway_reply: Callable[..., Awaitable[None]],
        backfill_napcat_history_for_day: Callable[[str], Awaitable[int]],
        load_day_transcript_rows: Callable[[str], list[Dict[str, Any]]],
        get_runtime_owner_label: Callable[[], str],
        owner_ids: list[str],
        owner_shared_session_id: str,
        legacy_owner_private_session_ids: set[str],
        owner_shared_local_sources: set[str],
        qq_remote_sources: set[str],
        get_active_character_context: Callable[[], tuple[str, str, str]],
    ) -> None:
        self.brain = brain
        self.event_bus = event_bus
        self.presenter = presenter
        self.logger = logger
        self.add_memory_safe = add_memory_safe
        self.emit_idle_status_when_safe = emit_idle_status_when_safe
        self.send_gateway_reply = send_gateway_reply
        self.backfill_napcat_history_for_day = backfill_napcat_history_for_day
        self.load_day_transcript_rows = load_day_transcript_rows
        self.get_runtime_owner_label = get_runtime_owner_label
        self.owner_ids = [str(item).strip() for item in owner_ids if str(item).strip()]
        self.owner_shared_session_id = owner_shared_session_id
        self.legacy_owner_private_session_ids = legacy_owner_private_session_ids
        self.owner_shared_local_sources = owner_shared_local_sources
        self.qq_remote_sources = qq_remote_sources
        self.get_active_character_context = get_active_character_context

    async def emit_diary_failure_reply(
        self,
        failure_text: str,
        ctx: Dict[str, Any],
        output_profile: Optional[Dict[str, Any]],
    ) -> None:
        profile = output_profile or build_output_profile(
            str((ctx or {}).get("source") or "text_input")
        )
        if profile.get("ui_append", True):
            await self.event_bus.emit("ui.append", role="assistant", text=failure_text)
        await self.presenter.present(
            failure_text,
            emotion="neutral",
            speak=profile.get("speak", True),
            show_bubble=profile.get("show_bubble", True),
        )
        await self.send_gateway_reply(failure_text, ctx, emotion="neutral")

    async def handle_diary_request(
        self,
        *,
        user_text: str,
        ctx: Dict[str, Any],
        output_profile: Optional[Dict[str, Any]],
        memory_path: str,
        target_date: Optional[date] = None,
        report_data: Any = None,
        raw_stats: Optional[Dict[str, Any]] = None,
        is_makeup: bool = False,
    ) -> None:
        diary_text = await self.summarize_day(
            report_data=report_data,
            raw_stats=raw_stats,
            auto=False,
            target_date=target_date,
            output_profile=output_profile,
        )
        if not diary_text:
            failure_date = (target_date or datetime.now().date()).strftime("%Y-%m-%d")
            failure_text = diary_utils.build_diary_failure_text(
                failure_date, is_makeup
            )
            await self.emit_diary_failure_reply(failure_text, ctx, output_profile)
        asyncio.create_task(
            self.add_memory_safe("user", user_text, meta={"path": memory_path})
        )
        await self.emit_idle_status_when_safe(
            output_profile,
            reason="summary_complete",
            had_presenter_output=True,
        )

    def _load_day_transcript_rows(self, date_str: str) -> list[Dict[str, Any]]:
        store = getattr(self.brain, "sqlite_store", None)
        return diary_utils.load_day_transcript_rows(
            store,
            date_str,
            on_error=lambda exc: print(f"[ChatService] Load day transcript failed: {exc}"),
        )

    def _fetch_day_chat_history(self, date_str: str) -> str:
        return diary_utils.fetch_day_chat_history(
            self.load_day_transcript_rows(date_str),
            date_str,
            owner_shared_session_id=self.owner_shared_session_id,
            legacy_owner_private_session_ids=self.legacy_owner_private_session_ids,
            owner_shared_local_sources=self.owner_shared_local_sources,
            qq_remote_sources=self.qq_remote_sources,
        )

    def _fetch_day_owner_chat_history(self, date_str: str, mode: str = "all") -> str:
        return diary_utils.fetch_day_owner_chat_history(
            self.load_day_transcript_rows(date_str),
            date_str,
            mode=mode,
            owner_shared_session_id=self.owner_shared_session_id,
            legacy_owner_private_session_ids=self.legacy_owner_private_session_ids,
            owner_shared_local_sources=self.owner_shared_local_sources,
            qq_remote_sources=self.qq_remote_sources,
        )

    def _get_runtime_owner_label(self) -> str:
        return self.get_runtime_owner_label()

    def _resolve_diary_subject_label(self) -> str:
        store = getattr(self.brain, "sqlite_store", None)
        candidates: list[str] = []

        if store:
            try:
                user_items = store.list_items(
                    status="active", type_="user_profile", limit=200, offset=0
                )
                for item in user_items:
                    if not isinstance(item, dict):
                        continue
                    tags = item.get("tags") or []
                    if "role:user" in tags and "name" in tags:
                        candidates.append(item.get("text"))
            except Exception:
                pass

            try:
                profile = store.get_profile()
                if isinstance(profile, dict):
                    candidates.append(profile.get("name"))
            except Exception:
                pass

            owner_profiles: list[Dict[str, Any]] = []
            for owner_id in self.owner_ids:
                try:
                    owner_profile = store.get_qq_user_profile(owner_id)
                except Exception:
                    owner_profile = None
                if owner_profile:
                    owner_profiles.append(owner_profile)

            if not owner_profiles and hasattr(store, "list_qq_user_profiles"):
                try:
                    profiles = store.list_qq_user_profiles(limit=50) or []
                    owner_profiles = [
                        item for item in profiles if isinstance(item, dict) and item.get("is_owner")
                    ]
                except Exception:
                    owner_profiles = []

            for owner_profile in owner_profiles:
                candidates.append(owner_profile.get("remark_name"))
                candidates.append(owner_profile.get("nickname"))

        candidates.append(self.get_runtime_owner_label())

        for value in candidates:
            label = str(value or "").strip()
            if label:
                return label
        return "你"

    def _find_existing_daily_log_id(
        self, store: Any, date_str: str, active_char_id: str
    ) -> str:
        if store is None:
            return ""
        try:
            with store._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id
                    FROM episodes
                    WHERE tags_json LIKE ? AND tags_json LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (f"%date:{date_str}%", f"%role:{active_char_id}%"),
                ).fetchone()
            if row:
                return str(row["id"] or "").strip()
        except Exception:
            return ""
        return ""

    async def summarize_day(
        self,
        report_data: str = None,
        raw_stats: Optional[Dict[str, Any]] = None,
        auto: bool = False,
        target_date: date = None,
        output_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not target_date:
            target_date = datetime.now().date()

        date_str = target_date.strftime("%Y-%m-%d")
        is_makeup = target_date < datetime.now().date()

        print(f"[Diary] Build summary ({date_str}) | makeup={is_makeup}")

        store = getattr(self.brain, "sqlite_store", None)

        stats_payload = (
            raw_stats
            if isinstance(raw_stats, dict)
            else (report_data if isinstance(report_data, dict) else None)
        )
        normalized_stats_payload = dict(stats_payload) if isinstance(stats_payload, dict) else None
        if normalized_stats_payload and not normalized_stats_payload.get("date"):
            normalized_stats_payload["date"] = date_str

        report_text = report_data
        if isinstance(report_data, dict):
            report_text = report_data.get(
                "summary_text", json.dumps(report_data, ensure_ascii=False)
            )
        elif not report_text and isinstance(raw_stats, dict):
            report_text = raw_stats.get(
                "summary_text", json.dumps(raw_stats, ensure_ascii=False)
            )

        if diary_utils.is_suspicious_daily_stats(
            date_str, normalized_stats_payload, str(report_text or "")
        ):
            self.logger.warning(
                f"Diary build detected suspicious stats for {date_str}; drop malformed screen summary."
            )
            normalized_stats_payload = None
            report_text = ""

        if store and isinstance(normalized_stats_payload, dict):
            try:
                if "summary_text" not in normalized_stats_payload:
                    normalized_stats_payload["summary_text"] = json.dumps(
                        normalized_stats_payload, ensure_ascii=False
                    )
                await asyncio.to_thread(
                    store.save_daily_screen_stats, date_str, normalized_stats_payload
                )
                print(f"[Diary] Screen stats saved: {date_str}")
            except Exception as e:
                print(f"[Diary] Screen stats save failed: {e}")

        if not report_text and store:
            report_text = await asyncio.to_thread(
                store.format_screen_stats_for_prompt, date_str
            )
            if diary_utils.is_suspicious_daily_stats(date_str, None, report_text):
                self.logger.warning(
                    f"Diary build skipped suspicious persisted screen summary for {date_str}."
                )
                report_text = ""
            elif not normalized_stats_payload:
                try:
                    persisted_stats = await asyncio.to_thread(
                        store.get_daily_screen_stats, date_str
                    )
                except Exception:
                    persisted_stats = None
                if isinstance(persisted_stats, dict) and not diary_utils.is_suspicious_daily_stats(
                    date_str,
                    persisted_stats,
                    str(persisted_stats.get("summary_text") or report_text or ""),
                ):
                    normalized_stats_payload = persisted_stats

        try:
            await self.backfill_napcat_history_for_day(date_str)
        except Exception as exc:
            self.logger.warning(
                f"NapCat history backfill skipped for {date_str}: {exc}"
            )

        chat_history = await asyncio.to_thread(self._fetch_day_chat_history, date_str)
        owner_chat_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str
        )
        owner_local_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str, "local"
        )
        owner_qq_private_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str, "qq_private"
        )
        owner_qq_group_history = await asyncio.to_thread(
            self._fetch_day_owner_chat_history, date_str, "qq_group"
        )

        chat_history = diary_utils.normalize_diary_text_block(chat_history)
        owner_chat_history = diary_utils.normalize_diary_text_block(owner_chat_history)
        owner_local_history = diary_utils.normalize_diary_text_block(owner_local_history)
        owner_qq_private_history = diary_utils.normalize_diary_text_block(
            owner_qq_private_history
        )
        owner_qq_group_history = diary_utils.normalize_diary_text_block(
            owner_qq_group_history
        )

        if not report_text and not chat_history and not owner_chat_history:
            print(f"[Diary] Skip {date_str}: no data")
            return ""

        active_char_name, active_char_id, base_prompt = self.get_active_character_context()
        active_char_name = active_char_name or "AI Assistant"
        active_char_id = active_char_id or "default_char"
        subject_label = self._resolve_diary_subject_label()
        daily_focus = diary_utils.build_diary_focus_digest(
            date_str,
            normalized_stats_payload,
            owner_local_history,
            owner_qq_private_history,
            owner_qq_group_history,
        )

        task_desc = f"你是 {active_char_name}。请根据记录，用简体中文写一篇你的日记，内容是你看到 {subject_label} 今天做了什么，以及你和 {subject_label} 发生了什么互动。日记主体是你自己，{subject_label} 是你观察和互动的对象。"
        if is_makeup:
            task_desc = f"你是 {active_char_name}。请根据记录，用简体中文补写一篇你的日记，内容是你在 {date_str} 看到 {subject_label} 做了什么，以及你和 {subject_label} 发生了什么互动。开头只需自然带出这是补写，不要单独另起标题。日记主体是你自己，{subject_label} 是你观察和互动的对象。"

        system_prompt = f"""
{base_prompt}

[任务]
{task_desc}

[数据源1：屏幕活动]
{report_text if report_text else "(none)"}

[数据源2：完整对话历史]
{chat_history if chat_history else "(none)"}

[数据源3：{subject_label}跨渠道聊天记录]
{owner_chat_history if owner_chat_history else f"(no {subject_label} local/QQ shared history today)"}

[数据源3a：{subject_label}本地聊天]
{owner_local_history if owner_local_history else "(none)"}

[数据源3b：{subject_label} QQ 私聊]
{owner_qq_private_history if owner_qq_private_history else "(none)"}

[数据源3c：{subject_label} QQ 群聊]
{owner_qq_group_history if owner_qq_group_history else "(none)"}

[当日关键点]
{daily_focus if daily_focus else "(none)"}

[输出要求]
用你（{active_char_name}）自己的语气和方式写，不要写成通用的流水账模板；以下为格式约束：
1. 必须只使用简体中文，不要输出英文段落、日文句子或混合语言。
2. 必须使用第一人称，并严格以“{active_char_name}”自己的视角来写；这里的“我”指的是“{active_char_name}”，不是 {subject_label}。
3. 你可以写“我看到 {subject_label} …… / 我陪着 {subject_label} …… / 我跟 {subject_label} 聊了…… / 我们一起……”，但不要把 {subject_label} 的行为直接写成“我今天打开了…… / 我今天去了…… / 我今天做了……”这种像是你亲自完成的表述。
4. 要包含具体细节，例如 {subject_label} 使用过的软件、你们讨论过的话题、你和 {subject_label} 发生过的互动。
5. 如果数据源3或3a/3b/3c不为空，要明确写出你和 {subject_label} 的本地聊天、QQ私聊、QQ群聊互动，并尽量区分这些场景。
6. 保持简洁，控制在 500 字以内。
7. 不要输出标题，不要输出项目符号，直接给出自然的一段或几段日记正文。
8. 数据源1里的屏幕内容只能当作“我看到 {subject_label} 在屏幕上做了什么/处理了什么”的线索，不能直接当作现实世界已经发生的事实。
9. 如果看到天气、锁屏壁纸、宣传文案、网页标题、窗口文字、桌面组件文案，只能写成“屏幕上出现了…… / 我看到 {subject_label} ……”，不要写成“窗外正在…… / 现实里正在……”。
10. 除非聊天记录里明确提到真实天气或真实环境，否则不要把屏幕里的天气文案改写成现实天气。
11. 在聊天记录中，只有 `Owner(Local)`、`Owner(QQ)` 明确代表 {subject_label} 本人；`OtherGroupMember(...)`、`OtherQQContact(...)` 都是别人，绝不能当作 {subject_label} 自己说的话或做的事。
12. `AI(to Owner)` 表示你和 {subject_label} 的直接互动；`AI(to QQ Group)`、`AI(to QQ Contact)` 表示你在和别人交流，不能反推成 {subject_label} 的个人行为。
13. 必须优先围绕“当日关键点”中至少 2 个具体点来写，避免把不同日期写成同一套模板。
14. 如果当天有效数据很少，就明确写“今天信息不多/互动不多”，不要用别的日期常见的活动来补足内容。
15. 默认写成 2 到 3 段短段落，段落之间空一行；不要整篇挤成一大段，也不要拆成很多碎段。
16. 第一段先写你对这一天的整体感受或开场印象，第二段再落到具体观察和互动，最后可以用一句较轻的收束。
17. 不要写成工作汇报、问题清单或分析报告，避免“今天的信息主要集中在……”“比较明确的一次互动是……”这种总结腔。
18. 开头不要单独输出日期、标题或“今日的日记，我……”，直接进入自然叙述。
		 """

        try:
            from modules.llm import chat_with_ai

            diary_content = await asyncio.to_thread(
                chat_with_ai,
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"请严格根据上面的数据与要求，以 {active_char_name} 的第一人称记录你观察到的 {subject_label} 的一天，以及你和 {subject_label} 的互动。只有 Owner(Local)/Owner(QQ) 才是 {subject_label} 本人，OtherGroupMember/OtherQQContact 都是别人；不要把别人的发言和行为记到 {subject_label} 身上，也不要把 {subject_label} 的行为直接写成你自己亲自做的事。请优先使用“当日关键点”里的当天独有细节，不要和前一天写成同一篇，直接输出日记正文。",
                    },
                ],
                task_type="summary",
                caller="daily_summary",
            )
            diary_content = (diary_content or "").strip()

            if diary_utils.is_invalid_diary_output(diary_content):
                self.logger.warning(
                    f"Diary build skipped invalid output ({date_str}): {diary_content[:180]}"
                )
                return ""

            diary_content = diary_utils.polish_diary_output(
                diary_content, date_str, is_makeup=is_makeup
            )
            if not diary_content:
                self.logger.warning(
                    f"Diary build produced empty polished output ({date_str})"
                )
                return ""

            title = f"{date_str} 日记"
            if is_makeup:
                title += " (补)"

            if store:
                episode_payload = {
                    "title": title,
                    "summary": diary_content,
                    "status": "active",
                    "tags": [
                        "daily_log",
                        f"role:{active_char_id}",
                        f"date:{date_str}",
                    ],
                    "created_at": datetime.now().isoformat(),
                }
                existing_id = self._find_existing_daily_log_id(
                    store, date_str, active_char_id
                )
                if existing_id:
                    episode_payload["id"] = existing_id
                store.upsert_episode(episode_payload)
                try:
                    stats = store.get_daily_screen_stats(date_str) or {}
                    stats["diary_done"] = True
                    store.save_daily_screen_stats(date_str, stats)
                except Exception as exc:
                    self.logger.warning(
                        f"Diary status flag update failed ({date_str}): {exc}"
                    )
            print(f"[Diary] Archived: {title}")

            asyncio.create_task(
                self.add_memory_safe(
                    "assistant",
                    f"【日记 {date_str}】{diary_content}",
                    meta={
                        "type": "episodic_memory",
                        "date": date_str,
                        "role": active_char_id,
                    },
                )
            )

            if not auto:
                profile = output_profile or build_output_profile("text_input")
                if profile.get("ui_append", True):
                    await self.event_bus.emit(
                        "ui.append", role="assistant", text=diary_content
                    )
                await self.presenter.present(
                    diary_content,
                    emotion="neutral",
                    interrupt=False,
                    speak=profile.get("speak", True),
                    show_bubble=profile.get("show_bubble", True),
                )
            return diary_content

        except Exception as e:
            self.logger.error(f"Diary build failed: {e}")
            return ""
