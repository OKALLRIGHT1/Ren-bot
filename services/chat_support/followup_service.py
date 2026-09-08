"""User-task agent and daily care/task followup prefaces."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Optional


class FollowupService:
    TASK_CREATE_KEYWORDS = (
        "待办",
        "todo",
        "to do",
        "提醒我",
        "记得",
        "别忘了",
        "要记得",
        "今天要",
        "明天要",
        "今晚要",
        "等会要",
        "待会要",
        "周末要",
        "计划",
        "打算",
        "安排",
        "准备",
        "要去",
        "需要",
        "得去",
        "我得",
        "我要",
    )
    TASK_DONE_KEYWORDS = (
        "做完了",
        "搞定了",
        "完成了",
        "弄完了",
        "处理完了",
        "解决了",
        "写完了",
        "提交了",
        "发了",
        "结束了",
        "done",
        "finished",
        "搞好了",
        "已经好了",
    )
    TASK_STATUS_KEYWORDS = (
        "任务",
        "待办",
        "todo",
        "进度",
        "安排",
        "计划",
        "打算",
        "提醒",
    )
    FOLLOWUP_TOPICS = {
        "health": (
            "腹泻",
            "拉肚子",
            "肚子疼",
            "胃痛",
            "发烧",
            "咳嗽",
            "头痛",
            "生病",
            "不舒服",
            "断食",
            "补液",
            "电解质",
            "医院",
            "就医",
        ),
        "sleep": (
            "熬夜",
            "失眠",
            "没睡好",
            "睡不着",
            "睡眠",
            "困",
            "早睡",
            "晚睡",
            "睡觉",
        ),
        "diet": (
            "没吃饭",
            "吃不下",
            "胃口",
            "饮食",
            "喝水",
            "脱水",
            "饿",
            "早餐",
            "午饭",
            "晚饭",
        ),
        "work_study": (
            "加班",
            "赶工",
            "ddl",
            "截止",
            "写代码",
            "开题",
            "报告",
            "复习",
            "考试",
            "作业",
            "项目",
        ),
        "emotion": (
            "焦虑",
            "压力",
            "难受",
            "烦",
            "崩溃",
            "低落",
            "紧张",
            "心情",
            "不开心",
            "累",
        ),
        "plan": (
            "明天",
            "计划",
            "打算",
            "安排",
            "要去",
            "准备",
            "目标",
            "待办",
        ),
    }
    CARE_FOLLOWUP_TOPICS = {"health", "sleep", "diet", "emotion"}

    def __init__(
        self,
        *,
        memory_store_getter: Callable[[], Any],
        add_memory_safe: Callable[..., Awaitable[Any]],
        logger: Any = None,
    ) -> None:
        self._memory_store_getter = memory_store_getter
        self.add_memory_safe = add_memory_safe
        self.logger = logger
        self.last_proactive_followup_day = ""
        self.last_task_followup_day = ""

    def _store(self):
        try:
            return self._memory_store_getter()
        except Exception:
            return None

    def _warn(self, message: str) -> None:
        if self.logger:
            self.logger.warning(message)

    @staticmethod
    def split_text_clauses(text: str) -> list[str]:
        if not text:
            return []
        return [
            seg.strip(" ，,、\t")
            for seg in re.split(r"[\n。！？!?；;]+", text or "")
            if seg.strip(" ，,、\t")
        ]

    @staticmethod
    def normalize_task_text(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        t = re.sub(r"^(待办|todo|to do)\s*[:：]?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"^(提醒我|记得|别忘了|要记得)\s*", "", t)
        t = re.sub(
            r"^(我(今天|明天|今晚|等会|待会|周末|之后|最近)?(要|得|想|准备|打算)|今天要|明天要|今晚要|等会要|待会要|周末要)\s*",
            "",
            t,
        )
        t = re.sub(r"^(安排|计划|打算|准备|需要|要去|得去)\s*", "", t)
        t = re.sub(r"(一下|一下子|这件事|这个事|这事)$", "", t)
        t = re.sub(r"^[：:、，,\-\s]+|[：:、，,。！？!?；;\s]+$", "", t)
        return t[:80].strip()

    def is_task_related_message(self, text: str) -> bool:
        lower = (text or "").strip().lower()
        if not lower:
            return False
        if any(k in lower for k in self.TASK_CREATE_KEYWORDS):
            return True
        if any(k in lower for k in self.TASK_DONE_KEYWORDS):
            return True
        if any(k in lower for k in self.TASK_STATUS_KEYWORDS):
            return True
        return False

    @staticmethod
    def looks_like_question_or_recall(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        lower = raw.lower()
        if "?" in raw or "？" in raw:
            return True
        if raw.rstrip().endswith(("吗", "么", "呢", "嘛")):
            return True
        if any(
            cue in lower
            for cue in (
                "还记得",
                "记得吗",
                "记得不",
                "你记得",
                "记得我",
                "记得上次",
                "记得之前",
            )
        ):
            return True
        return False

    def extract_task_candidates(self, text: str) -> list[str]:
        candidates = []
        seen = set()
        for raw in self.split_text_clauses(text):
            parts = [
                seg.strip(" ，,、\t")
                for seg in re.split(r"[，,、]+", raw)
                if seg.strip(" ，,、\t")
            ]
            for part in parts or [raw]:
                lower = part.lower()
                if len(part) < 4 or self.looks_like_question_or_recall(part):
                    continue
                if any(k in lower for k in self.TASK_DONE_KEYWORDS):
                    continue
                if not any(k in lower for k in self.TASK_CREATE_KEYWORDS):
                    continue
                create_hits = [k for k in self.TASK_CREATE_KEYWORDS if k in lower]
                if create_hits == ["记得"] and not any(
                    cue in lower
                    for cue in ("记得要", "记得帮", "记得把", "记得去", "记得给", "记得买")
                ):
                    continue
                cleaned = self.normalize_task_text(part)
                if len(cleaned) < 2:
                    continue
                if self.looks_like_question_or_recall(cleaned):
                    continue
                if cleaned in seen:
                    continue
                seen.add(cleaned)
                candidates.append(cleaned)
                if len(candidates) >= 2:
                    return candidates
        return candidates

    def extract_task_completion_hint(self, text: str) -> str:
        for raw in self.split_text_clauses(text):
            lower = raw.lower()
            if not any(k in lower for k in self.TASK_DONE_KEYWORDS):
                continue
            hint = raw
            for key in self.TASK_DONE_KEYWORDS:
                hint = re.sub(re.escape(key), "", hint, flags=re.IGNORECASE)
            hint = re.sub(r"^(这个|那个|这件事|这事|任务|待办)\s*", "", hint)
            hint = self.normalize_task_text(hint)
            return hint
        return ""

    def task_match_score(self, hint: str, task_text: str) -> float:
        hint_norm = self.normalize_task_text(hint)
        task_norm = self.normalize_task_text(task_text)
        if not task_norm:
            return -1.0
        if not hint_norm:
            return 0.1
        score = 0.0
        if hint_norm == task_norm:
            score += 8.0
        if hint_norm and hint_norm in task_norm:
            score += 5.0
        if task_norm and task_norm in hint_norm:
            score += 3.5
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", hint_norm):
            if token in task_norm:
                score += 1.0
        return score

    def find_matching_active_task(self, hint: str = "") -> Optional[Dict[str, Any]]:
        store = self._store()
        if not store:
            return None
        try:
            items = store.list_items(status="active", type_="todo", limit=12, offset=0)
        except Exception:
            return None
        if not items:
            return None
        if not hint:
            return items[0]
        best = None
        best_score = -1.0
        for idx, item in enumerate(items):
            score = self.task_match_score(hint, str(item.get("text") or "")) - (
                idx * 0.05
            )
            if score > best_score:
                best = item
                best_score = score
        return best if best_score >= 1.0 else None

    async def append_hidden_transcript_note(
        self, role: str, text: str, meta: Optional[Dict[str, Any]] = None
    ):
        store = self._store()
        note = str(text or "").strip()
        if not store or not note:
            return
        try:
            safe_meta = (meta or {}).copy()
            session_id = str(safe_meta.get("session_id") or "").strip() or None
            await asyncio.to_thread(
                store.add_transcript, role, note, safe_meta, None, session_id
            )
        except Exception as exc:
            self._warn(f"隐藏 transcript 记录失败: {exc}")

    async def upsert_user_tasks_from_text(self, user_text: str) -> list[str]:
        store = self._store()
        if not store:
            return []
        created = []
        for task_text in self.extract_task_candidates(user_text):
            existing = self.find_matching_active_task(task_text)
            if (
                existing
                and self.task_match_score(task_text, str(existing.get("text") or ""))
                >= 5.0
            ):
                item = dict(existing)
                item["text"] = task_text
                item["source"] = "task_agent"
                await asyncio.to_thread(store.upsert_item, item)
                created.append(task_text)
                continue
            item = {
                "type": "todo",
                "status": "active",
                "pin": any(k in task_text for k in ("今天", "明天", "ddl", "截止")),
                "confidence": 0.86,
                "tags": ["user_task", "auto"],
                "text": task_text,
                "source": "task_agent",
            }
            await asyncio.to_thread(store.upsert_item, item)
            created.append(task_text)
        return created

    async def complete_task_from_text(self, user_text: str) -> Optional[str]:
        store = self._store()
        if not store:
            return None
        lower = (user_text or "").strip().lower()
        if not any(k in lower for k in self.TASK_DONE_KEYWORDS):
            return None
        hint = self.extract_task_completion_hint(user_text)
        target = self.find_matching_active_task(hint)
        if not target:
            return None
        await asyncio.to_thread(
            store.set_item_status, str(target.get("id") or ""), "done"
        )
        return str(target.get("text") or "").strip() or None

    async def update_task_agent(self, user_text: str):
        completed = await self.complete_task_from_text(user_text)
        created = await self.upsert_user_tasks_from_text(user_text)
        return {"completed": completed, "created": created}

    @staticmethod
    def build_task_followup_text(task_text: str) -> str:
        del task_text
        return "昨天那件事，今天还要继续吗？"

    @staticmethod
    def is_short_life_task_text(text: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        hints = [
            "睡觉",
            "睡一觉",
            "去睡",
            "休息",
            "吃饭",
            "吃个饭",
            "洗澡",
            "洗个澡",
            "出门",
            "回家",
            "喝水",
            "上厕所",
            "午睡",
            "躺会儿",
        ]
        return any(hint in raw for hint in hints)

    @staticmethod
    def is_followup_worthy_task_item(item: Dict[str, Any]) -> bool:
        text = str((item or {}).get("text") or "").strip().lower()
        if not text:
            return False
        if bool((item or {}).get("pin")):
            return True
        worthy_hints = (
            "ddl",
            "deadline",
            "截止",
            "考试",
            "复习",
            "作业",
            "报告",
            "开题",
            "提交",
            "交稿",
            "报名",
            "面试",
            "打卡",
            "今天",
            "明天",
            "今晚",
            "这周",
            "周一",
            "周二",
            "周三",
            "周四",
            "周五",
            "周六",
            "周日",
        )
        return any(hint in text for hint in worthy_hints)

    @staticmethod
    def should_suppress_followup_preface(user_text: str) -> bool:
        raw = str(user_text or "").strip().lower()
        if not raw:
            return False
        high_priority_hints = [
            "摔",
            "摔倒",
            "受伤",
            "流血",
            "疼",
            "疼死",
            "崩溃",
            "难受",
            "不舒服",
            "发烧",
            "生病",
            "住院",
            "急",
            "出事",
            "救命",
            "害怕",
            "想哭",
            "焦虑",
            "头晕",
            "吐了",
        ]
        return any(hint in raw for hint in high_priority_hints)

    def has_today_task_followup(self) -> bool:
        store = self._store()
        if not store:
            return False
        today = datetime.now().date()
        try:
            rows = store.list_transcript(role="assistant", limit=220, offset=0)
            for row in rows:
                ts = int(row.get("ts", 0) or 0)
                if not ts or datetime.fromtimestamp(ts).date() != today:
                    continue
                meta = row.get("meta") or {}
                if str(meta.get("path") or "").strip() == "task_followup":
                    return True
        except Exception:
            return False
        return False

    def has_task_followup_for_item(self, item_id: str) -> bool:
        target = str(item_id or "").strip()
        if not target:
            return False
        store = self._store()
        if not store:
            return False
        try:
            rows = store.list_transcript(role="assistant", limit=420, offset=0)
            for row in rows:
                meta = row.get("meta") or {}
                if str(meta.get("path") or "").strip() != "task_followup":
                    continue
                if str(meta.get("item_id") or "").strip() == target:
                    return True
        except Exception:
            return False
        return False

    def find_task_followup_candidate(self) -> Optional[Dict[str, Any]]:
        store = self._store()
        if not store:
            return None
        try:
            items = store.list_items(status="active", type_="todo", limit=12, offset=0)
        except Exception:
            return None
        if not items:
            return None
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        oldest_allowed = today - timedelta(days=2)
        fallback = None
        for item in items:
            updated_at = str(item.get("updated_at") or "").strip()
            item_text = str(item.get("text") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if self.is_short_life_task_text(item_text):
                continue
            if not self.is_followup_worthy_task_item(item):
                continue
            if item_id and self.has_task_followup_for_item(item_id):
                continue
            try:
                updated_date = datetime.fromisoformat(
                    updated_at.replace("Z", "+00:00")
                ).date()
            except Exception:
                updated_date = None
            if updated_date and updated_date < oldest_allowed:
                continue
            if updated_date == yesterday:
                return item
            if updated_date and updated_date < today and fallback is None:
                fallback = item
        return fallback

    async def maybe_send_task_followup(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        try:
            source = str((ctx or {}).get("source", ""))
            if source and source != "text_input":
                return None
            if bool((ctx or {}).get("codex_mode", False)):
                return None
            today_str = datetime.now().strftime("%Y-%m-%d")
            if self.last_task_followup_day == today_str:
                return None
            if self.should_suppress_followup_preface(user_text or ""):
                return None
            if self.is_task_related_message(user_text or ""):
                return None
            if self.has_today_task_followup():
                self.last_task_followup_day = today_str
                return None
            candidate = self.find_task_followup_candidate()
            if not candidate:
                return None
            task_text = str(candidate.get("text") or "").strip()
            if not task_text:
                return None
            self.last_task_followup_day = today_str
            return {
                "text": self.build_task_followup_text(task_text),
                "task": task_text,
                "item_id": str(candidate.get("id") or ""),
            }
        except Exception as exc:
            self._warn(f"任务跟进触发失败: {exc}")
        return None

    def match_followup_topic(self, text: str) -> str:
        raw = (text or "").strip().lower()
        if not raw:
            return ""
        for topic, keywords in self.FOLLOWUP_TOPICS.items():
            if any(keyword in raw for keyword in keywords):
                return topic
        return ""

    @staticmethod
    def render_followup_label(topic: str, text: str) -> str:
        raw = (text or "").strip()
        if topic == "health":
            if "腹泻" in raw or "拉肚子" in raw:
                return "腹泻"
            if "断食" in raw:
                return "断食和补液"
            return "身体情况"
        if topic == "sleep":
            return "休息和睡眠"
        if topic == "diet":
            return "饮食和补水"
        if topic == "work_study":
            return "工作/学习进度"
        if topic == "emotion":
            return "心情和压力"
        if topic == "plan":
            return "你的计划安排"
        return "近况"

    @staticmethod
    def build_followup_text(topic: str, label: str) -> str:
        del label
        if topic == "health":
            return "今天身体还行吗？"
        if topic == "sleep":
            return "今天精神还撑得住吗？"
        if topic == "diet":
            return "今天有好好吃点东西吗？"
        if topic == "work_study":
            return ""
        if topic == "emotion":
            return "今天状态缓过来一点了吗？"
        if topic == "plan":
            return ""
        return ""

    async def record_proactive_followup(
        self, followup: Optional[Dict[str, Any]]
    ) -> None:
        if not isinstance(followup, dict):
            return
        text = str(followup.get("text") or "").strip()
        topic = str(followup.get("topic") or "").strip()
        snippet = str(followup.get("snippet") or "").strip()
        if not text:
            return
        note = text
        if snippet:
            note = f"{text}（主题:{topic or 'unknown'}，依据：{snippet}）"
        await self.append_hidden_transcript_note(
            "assistant",
            note,
            meta={
                "path": "proactive_followup",
                "topic": topic,
                "hidden": True,
            },
        )
        await self.add_memory_safe(
            "assistant",
            note,
            meta={
                "path": "proactive_followup",
                "topic": topic,
                "hidden": True,
            },
        )

    async def record_task_followup(self, followup: Optional[Dict[str, Any]]) -> None:
        if not isinstance(followup, dict):
            return
        text = str(followup.get("text") or "").strip()
        task_text = str(followup.get("task") or "").strip()
        item_id = str(followup.get("item_id") or "").strip()
        if not text:
            return
        note = text if not task_text else f"{text}（任务:{task_text}）"
        await self.append_hidden_transcript_note(
            "assistant",
            note,
            meta={
                "path": "task_followup",
                "task": task_text,
                "item_id": item_id,
                "hidden": True,
            },
        )
        await self.add_memory_safe(
            "assistant",
            note,
            meta={
                "path": "task_followup",
                "task": task_text,
                "item_id": item_id,
                "hidden": True,
            },
        )

    def find_yesterday_followup_note(self) -> tuple[str, str, str]:
        store = self._store()
        if not store:
            return "", "", ""
        now_dt = datetime.now()
        today = now_dt.date()
        yesterday = today - timedelta(days=1)
        max_age = timedelta(hours=18)
        try:
            rows = store.list_transcript(limit=420, offset=0)
            for row in rows:
                if (row.get("role") or "").strip() != "user":
                    continue
                ts = int(row.get("ts", 0) or 0)
                if not ts:
                    continue
                msg_dt = datetime.fromtimestamp(ts)
                if msg_dt.date() != yesterday:
                    continue
                if (now_dt - msg_dt) > max_age:
                    continue
                content = str(row.get("content") or "").strip()
                if not content:
                    continue
                meta = row.get("meta") or {}
                path = str(meta.get("path") or "").strip().lower()
                if path in {
                    "direct",
                    "tool_use",
                    "proactive_followup",
                    "task_followup",
                }:
                    continue
                if content.startswith("/"):
                    continue
                topic = self.match_followup_topic(content)
                if not topic:
                    continue
                if topic not in self.CARE_FOLLOWUP_TOPICS:
                    continue
                label = self.render_followup_label(topic, content)
                snippet = content[:80] + ("..." if len(content) > 80 else "")
                return topic, label, snippet
        except Exception:
            return "", "", ""
        return "", "", ""

    def has_today_proactive_followup(self) -> bool:
        store = self._store()
        if not store:
            return False
        today = datetime.now().date()
        try:
            rows = store.list_transcript(role="assistant", limit=260, offset=0)
            for row in rows:
                ts = int(row.get("ts", 0) or 0)
                if not ts:
                    continue
                if datetime.fromtimestamp(ts).date() != today:
                    continue
                content = str(row.get("content") or "")
                meta = row.get("meta") or {}
                if str(meta.get("path") or "").strip() == "proactive_followup":
                    return True
                if "[主动关心]" in content:
                    return True
        except Exception:
            return False
        return False

    async def maybe_send_proactive_followup(
        self, user_text: str, ctx: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        try:
            source = str((ctx or {}).get("source", ""))
            if source and source != "text_input":
                return None
            if bool((ctx or {}).get("codex_mode", False)):
                return None

            today_str = datetime.now().strftime("%Y-%m-%d")
            if self.last_proactive_followup_day == today_str:
                return None
            if self.should_suppress_followup_preface(user_text or ""):
                return None
            if datetime.now().hour >= 12:
                return None
            if self.match_followup_topic(user_text or ""):
                return None
            if self.has_today_proactive_followup():
                self.last_proactive_followup_day = today_str
                return None

            topic, label, snippet = self.find_yesterday_followup_note()
            if not topic:
                return None
            followup_text = self.build_followup_text(topic, label)
            if not followup_text:
                return None
            self.last_proactive_followup_day = today_str
            return {"text": followup_text, "topic": topic, "snippet": snippet}
        except Exception as exc:
            self._warn(f"主动关心触发失败: {exc}")
        return None
