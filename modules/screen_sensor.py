import time
import threading
import asyncio
import json
import re
import os
import random  # ✅ [新增] 引入随机模块

try:
    import pygetwindow as gw

    _PYGETWINDOW_OK = True
except Exception:
    gw = None
    _PYGETWINDOW_OK = False
from datetime import datetime
from typing import Optional, Dict, Tuple, List, Any

import config
from modules.llm import chat_with_ai

from config import (
    SCREEN_SENSOR_ENABLED,
    SCREEN_SENSOR_INTERVAL,
    SCREEN_DEBUG_VERBOSE,
    WINDOW_CATEGORIES,
    WINDOW_IGNORE_KEYWORDS,
    SCREEN_SMART_DEBOUNCE,
    SCREEN_REACTION_COOLDOWN,
    SCREEN_GLOBAL_COOLDOWN,
    SELF_WINDOW_TITLES,
    SEDENTARY_REMINDER_MINUTES,
    SEDENTARY_REMINDER_COOLDOWN_MINUTES,
    SCREEN_OBSERVATION_MAX_ITEMS,
    SCREEN_ACTIVITY_MAX_ITEMS,
)
from core.logger import get_logger

try:
    from modules.memory_sqlite import get_memory_store
except ImportError:
    get_memory_store = None


import ctypes
import os


# 定义 Windows 结构体，用于检测键鼠空闲时间
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_duration() -> float:
    """获取系统空闲时间（秒）"""
    if os.name == "nt":
        lastInputInfo = LASTINPUTINFO()
        lastInputInfo.cbSize = ctypes.sizeof(lastInputInfo)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lastInputInfo)):
            millis = ctypes.windll.kernel32.GetTickCount() - lastInputInfo.dwTime
            return millis / 1000.0
    return 0.0


class ScreenSensor:
    def __init__(self, chat_service):
        self.chat_service = chat_service
        self.logger = get_logger()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._loop = None

        # 状态记录
        self.last_window_title = ""
        self.last_app_name = ""
        self.last_category = None
        self.last_reaction_time = 0
        self.category_reaction_times = {}

        # [新增] 时长监控相关变量
        self.current_window_start_time = time.time()  # 当前窗口开始聚焦的时间
        self.next_duration_trigger_time = 0  # 下一次触发吐槽的时间点
        self.DURATION_TRIGGER_THRESHOLD = 20 * 60  # 阈值：连续 20 分钟没切屏触发一次

        # 数据文件
        self.stats_file = "./data/sensor_stats.json"

        # 核心数据
        self.daily_counts: Dict[str, int] = {}
        # 用来存时长 (单位: 秒)
        self.daily_durations: Dict[str, float] = {}
        self.app_cache: Dict[str, List[str]] = {}
        self.current_day = self._today_key()

        self.app_category_map: Dict[str, str] = {}
        self.observation_entries: List[Dict[str, Any]] = []
        self.activity_segments: List[Dict[str, Any]] = []
        self.max_observations = int(SCREEN_OBSERVATION_MAX_ITEMS)
        self.max_segments = int(SCREEN_ACTIVITY_MAX_ITEMS)

        self.sedentary_interval_sec = max(60, int(SEDENTARY_REMINDER_MINUTES) * 60)
        self.sedentary_cooldown_sec = max(
            300, int(SEDENTARY_REMINDER_COOLDOWN_MINUTES) * 60
        )
        self.next_sedentary_alert_time = time.time() + self.sedentary_interval_sec

        self._load_stats()
        self._last_alert_app = None
        self._last_alert_time = 0
        self.use_rust_events_only = True
        self._last_rust_event_id = ""
        self._last_rust_debug_key = ""
        self._last_rust_debug_at = 0.0
        self._last_rust_sample_ts = 0.0
        self.debug_verbose = bool(SCREEN_DEBUG_VERBOSE)

    def _debug_log(self, message: str):
        if self.debug_verbose:
            self.logger.info(message)

    def _today_key(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _stats_date_key(self) -> str:
        current = str(getattr(self, "current_day", "") or "").strip()
        return current or self._today_key()

    def _parse_rust_event_ts(self, item: Dict[str, Any]) -> float:
        raw_ts = str(item.get("ts") or "").strip()
        if not raw_ts:
            return 0.0
        normalized = raw_ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw_ts, fmt).timestamp()
            except ValueError:
                continue
        return 0.0

    def _rust_sample_seconds(self, sample_ts: float, now_ts: float) -> float:
        effective_ts = sample_ts if sample_ts > 0 else now_ts
        if self._last_rust_sample_ts <= 0:
            self._last_rust_sample_ts = effective_ts
            return 0.0
        delta = max(0.0, effective_ts - self._last_rust_sample_ts)
        self._last_rust_sample_ts = effective_ts
        max_sample_gap = max(5.0, float(SCREEN_SENSOR_INTERVAL) * 3.0)
        return min(delta, max_sample_gap)

    def start(self, loop):
        if not SCREEN_SENSOR_ENABLED:
            return
        if not _PYGETWINDOW_OK and not getattr(self, "use_rust_events_only", False):
            self.logger.warning("[ScreenSensor] pygetwindow 未安装，屏幕感知已禁用")
            return
        self._loop = loop
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self.logger.info("👀 [ScreenSensor] 启动完成 (含每日总结 + 视觉查岗)")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    # ==================== 数据接口 ====================
    def get_formatted_report(self) -> str:
        """生成今日数据的格式化文本 (含时长/分类/观察摘要)"""
        rust_lines = self._format_rust_events_summary(limit=10)
        if not self.daily_counts and not self.daily_durations and not rust_lines:
            return "（今日尚无任何屏幕活动记录）"

        sorted_apps = sorted(
            self.daily_durations.items(), key=lambda x: x[1], reverse=True
        )

        lines = []

        total_seconds = sum(self.daily_durations.values())
        total_hours = total_seconds / 3600.0
        lines.append(f"【今日屏幕活动统计】(活跃时长: {total_hours:.1f}小时)")

        category_totals = self._compute_category_totals()
        if category_totals:
            lines.append("【按分类】")
            for cat, seconds in sorted(
                category_totals.items(), key=lambda x: x[1], reverse=True
            ):
                if seconds <= 0:
                    continue
                lines.append(f"- {cat}: {self._format_duration(seconds)}")

        lines.append("【按应用】")
        for app, duration_sec in sorted_apps:
            count = self.daily_counts.get(app, 0)
            time_str = self._format_duration(duration_sec)
            lines.append(f"- {app}: {time_str} ({count}次)")

        obs_lines = self._format_compact_observations(
            self.observation_entries, limit=10
        )
        if obs_lines:
            lines.append("【今日观察摘要】")
            lines.extend(obs_lines)

        if self.activity_segments:
            lines.append("【最近活动切片】")
            recent_segments = self.activity_segments[-5:]
            for seg in recent_segments:
                start = seg.get("start_time", "--:--")
                end = seg.get("end_time", "--:--")
                app = seg.get("app", "")
                cat = seg.get("category", "")
                dur = self._format_duration(seg.get("duration_sec", 0))
                if app:
                    lines.append(f"- {start}-{end} {cat} | {app} ({dur})")
                else:
                    lines.append(f"- {start}-{end} {cat} ({dur})")

        if rust_lines:
            lines.append("【Rust 活动事件】")
            lines.extend(rust_lines)

        return "\n".join(lines)

    def _recent_rust_events(self, limit: int = 120) -> List[Dict[str, Any]]:
        if not get_memory_store:
            return []
        try:
            store = get_memory_store()
            if not store:
                return []
            return store.list_activity_events(limit=limit, date_str=self._stats_date_key())
        except Exception:
            return []

    def _format_rust_events_summary(self, limit: int = 12) -> List[str]:
        events = self._recent_rust_events(limit=limit)
        if not events:
            return []
        lines = []
        for item in reversed(events[-limit:]):
            ts = str(item.get("ts") or "")
            short_ts = ts[11:16] if len(ts) >= 16 else "--:--"
            app = str(((item.get("app") or {}).get("name") or "")).strip()
            title = str(item.get("window_title") or "").strip()
            domain = str((((item.get("browser") or {}).get("domain")) or "")).strip()
            detail = domain or title or app
            if detail:
                lines.append(f"- {short_ts} {app or 'unknown'} | {detail}")
        return lines

    def _process_rust_events_for_reaction(self, now_ts: float):
        events = self._recent_rust_events(limit=20)
        if not events:
            return

        newest_id = str(events[0].get("event_id") or "").strip()
        if not newest_id or newest_id == self._last_rust_event_id:
            return
        pending = []
        for item in events:
            event_id = str(item.get("event_id") or "").strip()
            if not event_id:
                continue
            pending.append(item)
            if event_id == self._last_rust_event_id:
                break
        pending.reverse()

        for latest in pending:
            event_id = str(latest.get("event_id") or "").strip()
            kind = str(latest.get("kind") or "").strip().lower()
            app = (
                str(((latest.get("app") or {}).get("name") or "")).strip() or "unknown"
            )
            event_ts = self._parse_rust_event_ts(latest) or now_ts
            title = str(latest.get("window_title") or "").strip()
            domain = str((((latest.get("browser") or {}).get("domain")) or "")).strip()
            full_title = domain or title or app
            cat, app_name = self._analyze_window(full_title)
            self._remember_app_category(app_name, cat)
            if kind == "foreground_changed":
                debug_key = f"{kind}:{app_name}:{cat}:{full_title[:80]}"
                if (
                    debug_key != self._last_rust_debug_key
                    or (now_ts - self._last_rust_debug_at) > 15
                ):
                    self._debug_log(
                        f"🦀 [Screen] Rust 事件命中: kind={kind} app={app_name} cat={cat} title={full_title[:80]}"
                    )
                    self._last_rust_debug_key = debug_key
                    self._last_rust_debug_at = now_ts

            if kind == "foreground_changed":
                self.daily_counts[app_name] = self.daily_counts.get(app_name, 0) + 1
                self.last_window_title = full_title
                self.last_app_name = app_name
                self.last_category = cat
                self.current_window_start_time = event_ts
                self.next_duration_trigger_time = event_ts + (20 * 60)
                self.next_sedentary_alert_time = event_ts + self.sedentary_interval_sec
                self._last_rust_sample_ts = event_ts
                self._last_alert_app = None
                self._debug_log("🦀 [Screen] Rust 事件按切屏逻辑尝试触发吐槽")
                self._try_trigger_reaction(
                    full_title,
                    cat,
                    self.daily_counts.get(app_name, 1),
                    app_name,
                    reason="switch",
                    app_duration_sec=self.daily_durations.get(app_name, 0.0),
                    current_stay_sec=max(0.0, event_ts - self.current_window_start_time),
                )
            elif kind == "activity_sample":
                sample_seconds = self._rust_sample_seconds(event_ts, now_ts)
                if sample_seconds > 0:
                    self.daily_durations[app_name] = self.daily_durations.get(
                        app_name, 0.0
                    ) + sample_seconds
                stay_minutes = max(
                    0, int((event_ts - self.current_window_start_time) / 60)
                )
                if event_ts >= self.next_duration_trigger_time:
                    self.next_duration_trigger_time = event_ts + (20 * 60)
                    self._debug_log(
                        f"🦀 [Screen] Rust 事件按停留逻辑尝试触发吐槽: stay={stay_minutes}min"
                    )
                    self._try_trigger_reaction(
                        full_title,
                        cat,
                        self.daily_counts.get(app_name, 1),
                        app_name,
                        reason="duration",
                        app_duration_sec=self.daily_durations.get(app_name, 0.0),
                        current_stay_sec=max(0.0, event_ts - self.current_window_start_time),
                    )
                if (
                    self.sedentary_interval_sec > 0
                    and event_ts >= self.next_sedentary_alert_time
                    and self._last_alert_app != app_name
                ):
                    self._debug_log(
                        f"🦀 [Screen] Rust 事件命中久坐提醒: app={app_name} stay={stay_minutes}min"
                    )
                    self._last_alert_app = app_name
                    self._last_alert_time = event_ts
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self.chat_service.send_active_alert(app_name, stay_minutes),
                            self._loop,
                        )
                    self.next_sedentary_alert_time = (
                        event_ts + self.sedentary_cooldown_sec
                    )

            self._last_rust_event_id = event_id

    def _format_duration(self, seconds: float) -> str:
        seconds = float(seconds or 0.0)
        if seconds < 60:
            return f"{int(seconds)}秒"
        if seconds < 3600:
            return f"{int(seconds / 60)}分钟"
        return f"{seconds / 3600:.1f}小时"

    def _parse_clock_to_minutes(self, value: str) -> Optional[int]:
        text_val = str(value or "").strip()
        if not text_val:
            return None
        try:
            parts = text_val.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            return hour * 60 + minute
        except Exception:
            return None

    def _compute_category_totals(self) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for app, seconds in self.daily_durations.items():
            app_name = str(app or "").strip()
            if not app_name:
                continue
            cat = self.app_category_map.get(app_name)
            if not cat:
                if "离开" in app_name or "idle" in app_name.lower():
                    cat = "away"
                else:
                    cat = "other"
            totals[cat] = totals.get(cat, 0.0) + float(seconds or 0.0)
        return totals

    def _normalize_record_text(self, text: str) -> str:
        text = str(text or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"`[^`]+`", " ", text)
        text = re.sub(r"[*#>\-_=~]+", " ", text)
        text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _is_low_value_record_text(self, text: str) -> bool:
        normalized = self._normalize_record_text(text)
        if len(normalized) < 12:
            return True

        low_value_patterns = (
            "看不清",
            "无法识别",
            "识别失败",
            "内容较少",
            "没有明显内容",
            "一个窗口",
            "一个界面",
            "屏幕截图",
            "当前屏幕",
            "未发现明确信息",
            "暂无更多信息",
            "未知内容",
            "不确定",
        )
        return any(pattern in normalized for pattern in low_value_patterns)

    def _is_similar_record(
        self, current_text: str, previous_text: str, threshold: float = 0.92
    ) -> bool:
        import difflib

        current = self._normalize_record_text(current_text)
        previous = self._normalize_record_text(previous_text)
        if not current or not previous:
            return False
        if current == previous:
            return True
        return difflib.SequenceMatcher(None, current, previous).ratio() >= threshold

    def _compress_recognition_text(self, text: str, max_length: int = 800) -> str:
        compressed = str(text or "").replace("\r\n", "\n").strip()
        if not compressed:
            return compressed

        compressed = re.sub(r"\n{3,}", "\n\n", compressed)
        lines = [line.strip() for line in compressed.split("\n") if line.strip()]
        if len(lines) > 8:
            compressed = "\n".join(lines[:8])
        else:
            compressed = "\n".join(lines)

        if len(compressed) > max_length:
            compressed = compressed[: max_length - 3].rstrip() + "..."

        return compressed

    def _remember_app_category(self, app_name: str, category: str) -> None:
        app_key = str(app_name or "").strip()
        if not app_key:
            return
        cat = str(category or "other").strip() or "other"
        if cat == "other" and app_key in self.app_category_map:
            return
        self.app_category_map[app_key] = cat

    def add_observation(
        self,
        content: str,
        window_title: str,
        category: str,
        app_name: str = "",
        reason: str = "",
        source: str = "text",
    ) -> bool:
        content = self._compress_recognition_text(content, max_length=800)
        if not content:
            content = str(window_title or "").strip()

        normalized = self._normalize_record_text(content)
        if self._is_low_value_record_text(normalized):
            return False

        recent_entries = list(self.observation_entries or [])[-5:]
        for prev in reversed(recent_entries):
            prev_text = prev.get("content", "")
            prev_window = prev.get("window_title", "")
            prev_category = prev.get("category", "")

            same_context = False
            if window_title and prev_window and window_title == prev_window:
                same_context = True
            elif category and prev_category and category == prev_category:
                same_context = True

            if same_context and self._is_similar_record(normalized, prev_text):
                return False

        now = time.time()
        entry = {
            "time": datetime.now().strftime("%H:%M"),
            "ts": now,
            "window_title": str(window_title or "").strip(),
            "app": str(app_name or window_title or "").strip(),
            "category": str(category or "").strip(),
            "reason": str(reason or "").strip(),
            "source": str(source or "text").strip(),
            "content": content,
        }
        self.observation_entries.append(entry)
        if len(self.observation_entries) > self.max_observations:
            self.observation_entries = self.observation_entries[
                -self.max_observations :
            ]
        return True

    def _append_activity_segment(
        self,
        app_name: str,
        category: str,
        start_ts: float,
        end_ts: float,
        reason: str = "switch",
    ) -> None:
        if not app_name:
            return
        if not start_ts or not end_ts or end_ts <= start_ts:
            return

        duration = float(end_ts - start_ts)
        if duration <= 1:
            return

        segment = {
            "start_time": datetime.fromtimestamp(start_ts).strftime("%H:%M"),
            "end_time": datetime.fromtimestamp(end_ts).strftime("%H:%M"),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_sec": duration,
            "app": str(app_name or "").strip(),
            "category": str(category or "other").strip() or "other",
            "reason": str(reason or "").strip(),
        }
        self.activity_segments.append(segment)
        if len(self.activity_segments) > self.max_segments:
            self.activity_segments = self.activity_segments[-self.max_segments :]

    def _compact_observations(
        self, entries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        compacted: List[Dict[str, Any]] = []
        for raw_entry in entries or []:
            entry_text = str(raw_entry.get("content") or "").strip()
            normalized_text = self._normalize_record_text(entry_text)
            if self._is_low_value_record_text(normalized_text):
                continue

            window_title = (
                str(raw_entry.get("window_title") or raw_entry.get("app") or "").strip()
                or "当前窗口"
            )
            time_text = str(raw_entry.get("time") or "").strip() or "--:--"
            entry_minutes = self._parse_clock_to_minutes(time_text)

            if compacted:
                previous = compacted[-1]
                same_window = previous["window_title"] == window_title
                last_minutes = previous.get("last_minutes")
                close_in_time = (
                    entry_minutes is not None
                    and last_minutes is not None
                    and entry_minutes - last_minutes <= 18
                )
                similar_to_previous = self._is_similar_record(
                    normalized_text,
                    previous.get("last_text", ""),
                    threshold=0.72,
                )
                if same_window and (close_in_time or similar_to_previous):
                    previous["end_time"] = time_text
                    previous["last_minutes"] = entry_minutes
                    if not previous["points"] or not self._is_similar_record(
                        normalized_text,
                        previous["points"][-1],
                        threshold=0.9,
                    ):
                        previous["points"].append(entry_text)
                    previous["last_text"] = normalized_text
                    continue

            compacted.append(
                {
                    "start_time": time_text,
                    "end_time": time_text,
                    "window_title": window_title,
                    "points": [entry_text],
                    "last_text": normalized_text,
                    "last_minutes": entry_minutes,
                }
            )

        return compacted

    def _format_compact_observations(
        self, entries: List[Dict[str, Any]], limit: int = 8
    ) -> List[str]:
        if not entries:
            return []
        compacted = self._compact_observations(entries)
        if not compacted:
            return []
        if limit and len(compacted) > limit:
            compacted = compacted[-limit:]

        lines = []
        for item in compacted:
            start = item.get("start_time", "--:--")
            end = item.get("end_time", "--:--")
            window_title = item.get("window_title", "")
            points = item.get("points", [])
            summary = "; ".join(points[:3])
            if len(points) > 3:
                summary = summary.rstrip() + "..."
            if start == end:
                time_range = start
            else:
                time_range = f"{start}-{end}"
            if summary:
                lines.append(f"- {time_range} {window_title}: {summary}")
            else:
                lines.append(f"- {time_range} {window_title}")
        return lines

    def get_recent_observations(self, limit: int = 3) -> List[Dict[str, Any]]:
        try:
            limit = int(limit)
        except Exception:
            limit = 3
        if limit <= 0:
            return []
        return list(self.observation_entries[-limit:])

    def get_stats_data(self) -> Dict[str, Any]:
        return {
            "date": self._stats_date_key(),
            "summary_text": self.get_formatted_report(),
            "counts": self.daily_counts,
            "durations": self.daily_durations,
            "category_totals": self._compute_category_totals(),
            "app_categories": self.app_category_map,
            "observations": self.observation_entries,
            "observation_compact": self._compact_observations(self.observation_entries),
            "segments": self.activity_segments,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _load_stats(self):
        if not os.path.exists(self.stats_file):
            return
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.app_cache = data.get("cache", {}) or {}
            self.app_category_map = data.get("app_categories", {}) or {}
            stored_date = str(data.get("date") or "").strip()
            if not stored_date:
                updated_at = str(data.get("updated_at") or "").strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}", updated_at):
                    stored_date = updated_at[:10]
            if stored_date == self._today_key():
                self.current_day = stored_date
                self.daily_counts = data.get("counts", {}) or {}
                self.daily_durations = data.get("durations", {}) or {}
                self.observation_entries = data.get("observations", []) or []
                self.activity_segments = data.get("segments", []) or []
            else:
                if not stored_date:
                    self.logger.warning(
                        "⚠️ [ScreenSensor] 发现缺少 date 的旧版统计缓存，已忽略其每日数据以避免串天。"
                    )
                self.current_day = self._today_key()
                self.daily_counts = {}
                self.daily_durations = {}
                self.observation_entries = []
                self.activity_segments = []

            if len(self.observation_entries) > self.max_observations:
                self.observation_entries = self.observation_entries[
                    -self.max_observations :
                ]
            if len(self.activity_segments) > self.max_segments:
                self.activity_segments = self.activity_segments[-self.max_segments :]
        except Exception:
            pass

    def _save_stats(self):
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            if len(self.observation_entries) > self.max_observations:
                self.observation_entries = self.observation_entries[
                    -self.max_observations :
                ]
            if len(self.activity_segments) > self.max_segments:
                self.activity_segments = self.activity_segments[-self.max_segments :]
            stats_date = self._stats_date_key()
            data = {
                "date": stats_date,
                "day": int(stats_date[-2:]) if len(stats_date) >= 10 else datetime.now().day,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "counts": self.daily_counts,
                "durations": self.daily_durations,
                "cache": self.app_cache,
                "app_categories": self.app_category_map,
                "category_totals": self._compute_category_totals(),
                "observations": self.observation_entries,
                "segments": self.activity_segments,
            }
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self._sync_to_db()
        except Exception:
            pass

    def _sync_to_db(self):
        """将屏幕统计数据同步到 SQLite 数据库"""
        if not get_memory_store:
            return
        try:
            store = get_memory_store()
            if store:
                stats_date = self._stats_date_key()

                # 计算总时长 (小时)
                total_seconds = sum(self.daily_durations.values())
                total_hours = total_seconds / 3600.0

                data_to_save = {
                    "date": stats_date,
                    "summary_text": self.get_formatted_report(),
                    "counts": self.daily_counts,
                    "durations": self.daily_durations,
                    "total_hours": total_hours,
                    "cache": self.app_cache,
                    "app_categories": self.app_category_map,
                    "category_totals": self._compute_category_totals(),
                    "observations": self.observation_entries,
                    "segments": self.activity_segments,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

                store.save_daily_screen_stats(stats_date, data_to_save)
        except Exception as e:
            self.logger.error(f"❌ 屏幕数据同步 DB 失败: {e}")

    def _get_active_window_info(self):
        """
        获取当前活动窗口，并检测是否为全屏状态
        返回: (窗口标题: str, 是否全屏: bool)
        """
        if self.use_rust_events_only and self._recent_rust_events(limit=1):
            return None, False
        if gw is None:
            return None, False
        try:
            win = gw.getActiveWindow()
            if win:
                t = win.title.strip()
                for kw in WINDOW_IGNORE_KEYWORDS:
                    if kw.lower() in t.lower():
                        return None, False

                # 🟢 检测是否全屏
                is_fullscreen = False
                if os.name == "nt":
                    # 获取主屏幕分辨率
                    screen_width = ctypes.windll.user32.GetSystemMetrics(0)
                    screen_height = ctypes.windll.user32.GetSystemMetrics(1)
                    # 容差：无边框全屏游戏可能会比分辨率大一点，或者正好等于
                    if win.width >= screen_width and win.height >= screen_height:
                        is_fullscreen = True

                return t, is_fullscreen
        except Exception:
            pass
        return None, False

    def _get_user_context(self) -> str:
        """从 SQLite 读取用户画像(Profile)，辅助窗口分类"""
        if not get_memory_store:
            return ""

        try:
            store = get_memory_store()
            if not store:
                return ""

            # 使用 memory_sqlite.py 中定义的 get_profile 方法
            # 它会返回 {'name':..., 'likes':..., 'dislikes':..., 'notes':...}
            p = store.get_profile()

            likes = p.get("likes", [])
            # 兼容处理: 如果是新版字典结构 {'music':[], 'games':[]}，转为列表
            if isinstance(likes, dict):
                flat_likes = []
                for k, v in likes.items():
                    if isinstance(v, list):
                        flat_likes.extend(v)
                likes = flat_likes

            # 截取前 10 个喜好，避免 Prompt 太长
            likes_str = ", ".join([str(x) for x in likes[:10]])

            # 也可以读取 notes 获取职业信息，这里简单处理
            return f"用户喜好/职业关键词: {likes_str}"
        except Exception as e:
            self.logger.error(f"读取 Profile 失败: {e}")
            return ""

    def _ask_ai_to_classify(self, title: str) -> Tuple[str, str]:
        # 1. 获取用户背景 (例如：用户喜欢 Coding，那么 VSCode 就是 Work/Coding 而不是 Other)
        user_ctx = self._get_user_context()

        self.logger.info(f"🧠 [Screen] 询问 AI: {title}")

        prompt = f"""
        任务：分析当前活动窗口的类别。
        窗口标题："{title}"
        用户背景：{user_ctx}

        可选分类：
        - coding (编程, IDE, 终端, 技术文档)
        - gaming (游戏, Steam)
        - video (视频, 直播)
        - social (社交, 聊天)
        - work (办公, 文档, 会议)
        - design (设计, 画图)
        - browser (通用浏览)
        - other (其他)

        规则：
        1. 参考用户背景。如果用户是程序员，IDE属于coding；如果用户是画师，PS属于design。
        2. 绝对禁止输出代码块。
        3. 仅输出 JSON 格式：{{"app": "软件简称", "cat": "分类代码"}}
        """
        try:
            # 使用 summary 路由，避免与 gatekeeper 抢占同一调用队列
            resp = chat_with_ai(
                [{"role": "user", "content": prompt}],
                task_type="screen_classify",
                caller="screen_classify",
            )

            # 提取 JSON
            match = re.search(r"\{.*?\}", resp, re.DOTALL)
            if match:
                d = json.loads(match.group(0))
                return d.get("app", "Unknown"), d.get("cat", "other")
        except Exception:
            pass
        return title, "other"

    def _analyze_window(self, title: str):
        # 1. 查缓存
        if title in self.app_cache:
            c = self.app_cache[title]
            return c[1], c[0]

        title_lower = title.lower()

        # =================================================
        # 🟢 [新增] 浏览器/视频感知 (Browser Awareness)
        # =================================================
        # Chrome/Edge 的标题通常是 "视频标题 - YouTube - Google Chrome"

        if " - youtube" in title_lower:
            # 提取视频标题
            video_title = title.split(" - YouTube")[0].strip()
            # 归类为 video，但 App 名直接用视频标题，方便 AI 识别
            # 存入缓存时，key 是完整标题，value 是 [处理后的标题, 分类]
            fake_app_name = f"YouTube: {video_title}"
            self.app_cache[title] = [fake_app_name, "video"]
            self._save_stats()
            return "video", fake_app_name

        if " - bilibili" in title_lower:
            video_title = title.split(" - Bilibili")[0].strip()
            fake_app_name = f"B站: {video_title}"
            self.app_cache[title] = [fake_app_name, "video"]
            self._save_stats()
            return "video", fake_app_name

        # =================================================
        # 🟢 [新增] 优先检测是否是“我自己”
        # =================================================
        for self_t in SELF_WINDOW_TITLES:
            # 只要包含关键词即可 (比如 "Live2D Agent" 包含 "L2D" 或完整匹配)
            if self_t.lower() in title_lower:
                # 存入缓存，分类标记为 "self"
                self.app_cache[title] = [title, "self"]
                self._save_stats()
                return "self", title
        # =================================================

        # 2. 查常规分类
        for cat, kws in WINDOW_CATEGORIES.items():
            for k in kws:
                if k.lower() in title_lower:
                    self.app_cache[title] = [k, cat]
                    self._save_stats()
                    return cat, k

        # 3. AI 分类 (兜底)
        if len(title) > 2:
            app, cat = self._ask_ai_to_classify(title)
            self.app_cache[title] = [app, cat]
            self._save_stats()
            return cat, app

        return "other", title

    def _check_daily_reset(self):
        today = self._today_key()
        if today != self.current_day:
            self.logger.info("📅 新的一天，开始结算昨日数据...")

            previous_day = self._stats_date_key()
            has_activity = bool(
                self.daily_counts
                or self.daily_durations
                or self.observation_entries
                or self.activity_segments
            )
            diary_done = False

            if has_activity and get_memory_store:
                try:
                    store = get_memory_store()
                    if store:
                        previous_stats = store.get_daily_screen_stats(previous_day) or {}
                        diary_done = previous_stats.get("diary_done") is True
                except Exception:
                    diary_done = False

            if has_activity and not diary_done:
                self.logger.info(
                    f"📦 [Screen] 跨天切日，昨日({previous_day})统计已保留，等待统一补录器归档日记。"
                )

            self.daily_counts.clear()
            self.daily_durations.clear()
            self.observation_entries = []
            self.activity_segments = []
            self._last_rust_sample_ts = 0.0
            self.current_day = today
            self._save_stats()

    def _monitor_loop(self):
        """后台监控循环 (终极版：时间轴修正 + 挂机检测 + 精准免打扰)"""
        import config  # 放到循环内或顶部均可

        if getattr(self, "use_rust_events_only", False):
            self.logger.info(
                "🦀 [Screen] Rust sidecar 已启用，跳过 Python 本地窗口监控循环"
            )
            while self.running:
                try:
                    time.sleep(2)
                    self._check_daily_reset()
                    self._process_rust_events_for_reaction(time.time())
                    self._save_stats()
                except Exception as e:
                    self.logger.error(f"🦀 [Screen] Rust 模式循环异常: {e}")
            return

        # 如果初始化时没加，这里做个兜底防报错
        if not hasattr(self, "is_afk"):
            self.is_afk = False
            self.AFK_THRESHOLD_SEC = 300

        last_tick_time = time.time()

        while self.running:
            try:
                time.sleep(SCREEN_SENSOR_INTERVAL)

                now = time.time()
                elapsed = now - last_tick_time
                last_tick_time = now

                # 1. 休眠/卡顿溢出保护
                if elapsed > SCREEN_SENSOR_INTERVAL * 3:
                    self.logger.info(
                        f"💤 [Screen] 检测到系统休眠苏醒 (跳过 {elapsed:.1f}s)"
                    )
                    self.current_window_start_time = now
                    self.next_duration_trigger_time = (
                        now + self.DURATION_TRIGGER_THRESHOLD
                    )
                    self.next_sedentary_alert_time = now + self.sedentary_interval_sec
                    elapsed = SCREEN_SENSOR_INTERVAL

                self._check_daily_reset()

                # 2. 获取窗口与全屏状态
                current_title, is_fullscreen = self._get_active_window_info()

                # 3. 锁屏/无窗口保护
                if not current_title:
                    self.current_window_start_time = now
                    self.next_duration_trigger_time = (
                        now + self.DURATION_TRIGGER_THRESHOLD
                    )
                    self.next_sedentary_alert_time = now + self.sedentary_interval_sec
                    continue

                # 4. 分析分类
                cat, app = self._analyze_window(current_title)
                self._remember_app_category(app, cat)

                # 🟢 [核心修复] 精准免打扰逻辑：手动开启，或者 (处于全屏 且 必须是打游戏/看视频)
                is_dnd_active = getattr(config, "DND_MODE", False) or (
                    is_fullscreen and cat in ["gaming", "video"]
                )

                # ========================================================
                # 5. 动态挂机检测
                # ========================================================
                if (is_fullscreen and cat in ["gaming", "video"]) or cat == "video":
                    current_afk_threshold = (
                        7200  # 全屏游戏/视频，或普通视频：容忍 2 小时不动
                    )
                elif cat == "gaming":
                    current_afk_threshold = 1800  # 普通窗口游戏：容忍 30 分钟不动
                else:
                    current_afk_threshold = (
                        self.AFK_THRESHOLD_SEC
                    )  # 普通办公：容忍 5 分钟不动

                idle_sec = get_idle_duration()

                if idle_sec > current_afk_threshold:
                    if not self.is_afk:
                        self.logger.info(
                            f"🚶 [Screen] 离开电脑 (空闲 {int(idle_sec)}s，当前阈值 {current_afk_threshold}s)"
                        )
                        self.is_afk = True
                        if self.last_app_name:
                            self._append_activity_segment(
                                self.last_app_name,
                                self.last_category or "other",
                                self.current_window_start_time,
                                now,
                                reason="afk",
                            )

                    self.current_window_start_time = now
                    self.next_duration_trigger_time = (
                        now + self.DURATION_TRIGGER_THRESHOLD
                    )
                    self.next_sedentary_alert_time = now + self.sedentary_interval_sec
                    self.daily_durations["[离开电脑]"] = (
                        self.daily_durations.get("[离开电脑]", 0.0) + elapsed
                    )
                    self._save_stats()
                    continue

                elif self.is_afk:
                    self.logger.info("🏃 [Screen] 用户回来了！")
                    self.is_afk = False
                    self.current_window_start_time = now
                    self.next_duration_trigger_time = (
                        now + self.DURATION_TRIGGER_THRESHOLD
                    )
                    self.next_sedentary_alert_time = now + self.sedentary_interval_sec
                # ========================================================

                # 6. 正常累加当前软件时长
                self.daily_durations[app] = self.daily_durations.get(app, 0.0) + elapsed

                is_switch = app != self.last_app_name

                if is_switch:
                    # ========== 场景A: 切换窗口 ==========
                    if self.last_app_name:
                        self._append_activity_segment(
                            self.last_app_name,
                            self.last_category or "other",
                            self.current_window_start_time,
                            now,
                            reason="switch",
                        )
                    self.last_window_title = current_title
                    self.last_app_name = app
                    self.last_category = cat

                    self.current_window_start_time = now
                    self.next_duration_trigger_time = (
                        now + self.DURATION_TRIGGER_THRESHOLD
                    )
                    self.next_sedentary_alert_time = now + self.sedentary_interval_sec
                    self._last_alert_app = None

                    self.daily_counts[app] = self.daily_counts.get(app, 0) + 1
                    self._save_stats()

                    # 🟢 使用统一的精准免打扰拦截
                    if is_dnd_active:
                        self.logger.info(f"🔕 [Screen] 免打扰生效，静默记录切换: {app}")
                    else:
                        count = self.daily_counts[app]
                        self._try_trigger_reaction(
                            current_title,
                            cat,
                            count,
                            app,
                            reason="switch",
                            app_duration_sec=self.daily_durations.get(app, 0.0),
                            current_stay_sec=max(0.0, now - self.current_window_start_time),
                        )

                else:
                    # ========== 场景B: 停留 ==========
                    self._save_stats()
                    stay_minutes = int((now - self.current_window_start_time) / 60)

                    # 久坐提醒 (基于下一次触发时间戳)
                    if (
                        self.sedentary_interval_sec > 0
                        and now >= self.next_sedentary_alert_time
                    ):
                        if (
                            self._last_alert_app != app
                            or (now - self._last_alert_time)
                            > self.sedentary_cooldown_sec
                        ):
                            if is_dnd_active:
                                self.logger.info(
                                    f"🔕 [Active] 免打扰生效，跳过久坐语音: {app}"
                                )
                                self._last_alert_app = app
                                self._last_alert_time = now
                            else:
                                self.logger.info(
                                    f"⏰[Active] 触发久坐提醒: {app} ({stay_minutes} min)"
                                )
                                self._last_alert_app = app
                                self._last_alert_time = now
                                if self._loop:
                                    asyncio.run_coroutine_threadsafe(
                                        self.chat_service.send_active_alert(
                                            app, stay_minutes
                                        ),
                                        self._loop,
                                    )
                        self.next_sedentary_alert_time = (
                            now + self.sedentary_cooldown_sec
                        )

                    monitor_cats = ["gaming", "video", "coding", "work", "design"]
                    if cat in monitor_cats:
                        if now > self.next_duration_trigger_time:
                            self.next_duration_trigger_time = now + (30 * 60)
                            if is_dnd_active:
                                self.logger.info(
                                    f"🔕 [Screen] 免打扰生效，跳过沉浸查岗: <{app}>"
                                )
                            else:
                                self.logger.info(f"⏳ [Screen] 沉浸时长触发: <{app}>")
                                count = self.daily_counts.get(app, 1)
                                self._try_trigger_reaction(
                                    current_title,
                                    cat,
                                    count,
                                    app,
                                    reason="duration",
                                    app_duration_sec=self.daily_durations.get(app, 0.0),
                                    current_stay_sec=max(0.0, now - self.current_window_start_time),
                                )

            except Exception as e:
                self.logger.error(f"ScreenSensor error: {e}")
                time.sleep(SCREEN_SENSOR_INTERVAL)
                last_tick_time = time.time()

    def _try_trigger_reaction(
        self,
        full_title: str,
        category: str,
        count: int,
        app_name: str,
        reason: str = "switch",
        app_duration_sec: float | None = None,
        current_stay_sec: float | None = None,
    ):
        now = time.time()

        # 1. 基础冷却检查 (全局防刷屏)
        # 如果刚说完话不到 10 秒 (SCREEN_GLOBAL_COOLDOWN)，绝对闭嘴
        if now - self.last_reaction_time < SCREEN_GLOBAL_COOLDOWN:
            debug_key = f"global:{app_name}:{reason}"
            if (
                debug_key != self._last_rust_debug_key
                or (now - self._last_rust_debug_at) > 20
            ):
                self._debug_log(
                    f"🛑 [ScreenDebug] 全局冷却中，跳过吐槽: app={app_name} reason={reason}"
                )
                self._last_rust_debug_key = debug_key
                self._last_rust_debug_at = now
            return

        # 2. 智能防刷屏 (针对 switch 事件)
        # 这里的目的是：不要切太快，而不是限制“不说话”
        if reason == "switch" and SCREEN_SMART_DEBOUNCE:
            cd = SCREEN_REACTION_COOLDOWN
            # 只有当频率极高时才增加冷却，平时尽量放行
            if count > 5:
                cd *= 2
            if count > 20:
                cd *= 4

            # 如果还在分类冷却期内，直接跳过 (这是为了防止 ChatService 压力过大)
            if now - self.category_reaction_times.get(category, 0) < cd:
                debug_key = f"cat_cd:{app_name}:{category}:{reason}:{cd}"
                if (
                    debug_key != self._last_rust_debug_key
                    or (now - self._last_rust_debug_at) > 20
                ):
                    self._debug_log(
                        f"🛑 [ScreenDebug] 分类冷却中，跳过吐槽: app={app_name} cat={category} reason={reason} cd={cd}s"
                    )
                    self._last_rust_debug_key = debug_key
                    self._last_rust_debug_at = now
                return

            # 只有极高频次才进行概率静音
            should_talk = True
            if count > 20 and count % 10 != 0:
                should_talk = False
            if not should_talk:
                debug_key = f"high_freq:{app_name}:{count}"
                if (
                    debug_key != self._last_rust_debug_key
                    or (now - self._last_rust_debug_at) > 20
                ):
                    self._debug_log(
                        f"🛑 [ScreenDebug] 高频切换静音，跳过吐槽: app={app_name} count={count}"
                    )
                    self._last_rust_debug_key = debug_key
                    self._last_rust_debug_at = now
                return

        elif reason == "switch":
            if (
                now - self.category_reaction_times.get(category, 0)
                < SCREEN_REACTION_COOLDOWN
            ):
                debug_key = f"base_cd:{app_name}:{category}"
                if (
                    debug_key != self._last_rust_debug_key
                    or (now - self._last_rust_debug_at) > 20
                ):
                    self._debug_log(
                        f"🛑 [ScreenDebug] 分类基础冷却中，跳过吐槽: app={app_name} cat={category}"
                    )
                    self._last_rust_debug_key = debug_key
                    self._last_rust_debug_at = now
                return

        # ============================================================
        # 3. 核心修改：视觉判定逻辑分离
        # 默认：只是普通文本观察 (use_vision = False)
        # ============================================================
        use_vision = False

        # 场景 A: 沉浸时长触发 (reason="duration")
        # 既然看了这么久没动，大概率是有内容的，强制视觉查岗
        if reason == "duration":
            use_vision = True
            self.logger.info(f"📸 [Sensor] 触发视觉查岗 (原因: 长时间停留)")

        # 场景 B: 切换触发 (reason="switch") -> 掷骰子决定是否升级为视觉
        else:
            interesting_cats = [
                "gaming",
                "video",
                "social",
                "design",
                "coding",
                "work",
                "other",
            ]

            # 只有在这些分类下，才有概率“升级”为截图
            if category in interesting_cats:
                base_prob = 0.15
                prob_boost = count * 0.05
                final_prob = min(base_prob + prob_boost, 0.85)

                # 掷骰子！
                if random.random() < final_prob:
                    use_vision = True
                    self.logger.info(
                        f"🎲 [Sensor] 运气爆棚！升级为视觉查岗 (概率: {final_prob:.2f})"
                    )
                else:
                    # 没摇中，仅作为普通文本事件处理
                    # self.logger.info(f"🎲 [Sensor] 只是普通观察 (未触发视觉)")
                    pass

        self.logger.info(
            f"👀 [Screen] 触发 ChatService: {app_name} | Vision: {use_vision}"
        )

        # 4. 执行发送
        # 无论 use_vision 是 True 还是 False，都发送给 ChatService
        # - True  -> ChatService 调用 Smart Model (看图+吐槽)
        # - False -> ChatService 调用 Gatekeeper (判断是否无聊 -> 决定是否吐槽)
        if self._loop and self._loop.is_running():
            if app_duration_sec is None:
                app_duration_sec = self.daily_durations.get(app_name, 0.0)
            if current_stay_sec is None:
                current_stay_sec = max(0.0, time.time() - self.current_window_start_time)
            asyncio.run_coroutine_threadsafe(
                self.chat_service.handle_sensor_event(
                    full_title,
                    category,
                    count,
                    use_vision=use_vision,
                    app_name=app_name,
                    reason=reason,
                    app_duration_sec=app_duration_sec,
                    current_stay_sec=current_stay_sec,
                ),
                self._loop,
            )

        self.last_reaction_time = now
        self.category_reaction_times[category] = now
