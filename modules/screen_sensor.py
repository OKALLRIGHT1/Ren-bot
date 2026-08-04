import time
import threading
import asyncio
import json
import re
import os

from datetime import datetime
from typing import Optional, Dict, Tuple, List, Any

import config
from modules.screen_app_registry import ScreenAppRegistry

from config import (
    SCREEN_SENSOR_ENABLED,
    SCREEN_SENSOR_INTERVAL,
    SCREEN_DEBUG_VERBOSE,
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
from services.runtime_health import get_runtime_health

try:
    from modules.memory_sqlite import get_memory_store
except ImportError:
    get_memory_store = None


WORK_SESSION_EVENT_LIMIT = 1200
WORK_SESSION_RESTART_BRIDGE_SEC = 15 * 60
WORK_SESSION_MAX_RUST_PAYLOAD_MINUTES = 12 * 60
LIVE2D_ACTIVITY_SOURCE = "live2d-tauri"
LIVE2D_SEDENTARY_SOURCE = "live2d-sedentary"
SEDENTARY_SESSION_APP_NAME = "电脑"
SEDENTARY_SESSION_CATEGORY = "computer_active"








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
        self.next_duration_trigger_time = 0  # 下一次触发吐槽的时间点?
        self.DURATION_TRIGGER_THRESHOLD = 20 * 60  # 阈值：连续 20 分钟没切屏触发一次

        # 数据文件
        self.stats_file = "./data/sensor_stats.json"

        # 鏍稿績鏁版嵁
        self.daily_counts: Dict[str, int] = {}
        # 鐢ㄦ潵瀛樻椂闀?(鍗曚綅: 绉?
        self.daily_durations: Dict[str, float] = {}
        self.app_cache: Dict[str, List[str]] = {}
        self.current_day = self._today_key()

        self.app_category_map: Dict[str, str] = {}
        self.app_registry = ScreenAppRegistry()
        self.observation_entries: List[Dict[str, Any]] = []
        self.activity_segments: List[Dict[str, Any]] = []
        self.max_observations = int(SCREEN_OBSERVATION_MAX_ITEMS)
        self.max_segments = int(SCREEN_ACTIVITY_MAX_ITEMS)

        self.sedentary_interval_sec = max(60, int(SEDENTARY_REMINDER_MINUTES) * 60)
        self.sedentary_cooldown_sec = max(
            300, int(SEDENTARY_REMINDER_COOLDOWN_MINUTES) * 60
        )
        self.next_sedentary_alert_time = time.time() + self.sedentary_interval_sec
        self.sedentary_session_start_ts = 0.0
        self.sedentary_session_source = LIVE2D_ACTIVITY_SOURCE
        self.sedentary_session_app_name = ""
        self.sedentary_session_category = ""

        self._last_rust_event_id = ""
        self._last_rust_processed_ts = 0.0
        self._last_rust_debug_key = ""
        self._last_rust_debug_at = 0.0
        self._last_rust_sample_ts = 0.0
        self._last_rust_event_seen_at = 0.0
        self._rust_health_state = ""
        self.runtime_health = get_runtime_health()
        self._suppress_stats_save = False
        self.debug_verbose = bool(SCREEN_DEBUG_VERBOSE)
        self._load_stats()
        self._last_alert_app = None
        self._last_alert_time = 0
        self._sedentary_startup_grace_until = time.time() + 120.0
        self.use_rust_events_only = True
        self._sedentary_popup_callback = None
        self._sedentary_meme_selector = None
        self._sedentary_popup_in_flight = False
        self._sedentary_user_suppressed_until = 0.0

    def set_sedentary_popup_callback(self, callback):
        self._sedentary_popup_callback = callback

    def set_sedentary_meme_selector(self, selector):
        self._sedentary_meme_selector = selector

    def _show_sedentary_popup(self, app_name: str, active_minutes: int) -> None:
        callback = self._sedentary_popup_callback
        if not callable(callback):
            return
        if self._sedentary_popup_in_flight:
            return
        self._sedentary_popup_in_flight = True

        def _on_result(result: str) -> None:
            now = time.time()
            if result == "snooze":
                try:
                    snooze_minutes = int(
                        getattr(config, "SEDENTARY_POPUP_SNOOZE_MINUTES", 10)
                    )
                except Exception:
                    snooze_minutes = 10
                delay_sec = max(1, snooze_minutes) * 60
            else:
                delay_sec = max(60.0, float(self.sedentary_cooldown_sec or 0))
            self.next_sedentary_alert_time = now + delay_sec
            self._sedentary_user_suppressed_until = now + delay_sec
            self._last_alert_time = now
            self._last_alert_app = SEDENTARY_SESSION_APP_NAME
            self._sedentary_popup_in_flight = False

        try:
            image_path = ""
            selector = self._sedentary_meme_selector
            if callable(selector):
                try:
                    future = selector(app_name, active_minutes)
                    if hasattr(future, "result"):
                        result = future.result(timeout=8)
                    else:
                        result = future
                    image_path = str(result or "")
                except Exception as exc:
                    self.logger.warning(f"[Screen] 久坐表情包选择失败: {exc}")
            callback(app_name, active_minutes, image_path, _on_result)
        except Exception as exc:
            self._sedentary_popup_in_flight = False
            self.logger.warning(f"[Screen] 久坐提醒弹窗触发失败: {exc}")

    def _trigger_sedentary_alert(
        self,
        *,
        now_ts: float,
        alert_app_name: str,
        active_minutes: int,
        app_name: str,
        category: str,
        full_title: str,
        source: str,
        log_label: str,
    ) -> bool:
        user_suppressed_until = float(
            getattr(self, "_sedentary_user_suppressed_until", 0.0) or 0.0
        )
        if user_suppressed_until > 0 and now_ts < user_suppressed_until:
            return False
        if (
            self._last_alert_app == alert_app_name
            and now_ts - self._last_alert_time <= self.sedentary_cooldown_sec
        ):
            return False
        if self._sedentary_popup_in_flight:
            return False

        is_dnd_active = getattr(config, "DND_MODE", False)
        self._last_alert_app = alert_app_name
        self._last_alert_time = now_ts
        self.next_sedentary_alert_time = now_ts + self.sedentary_cooldown_sec

        if now_ts < float(getattr(self, "_sedentary_startup_grace_until", 0.0) or 0.0):
            self.logger.info(
                f"⏰ [Active] 启动宽限期内跳过{log_label}: {alert_app_name} ({active_minutes} min)"
            )
            return True

        if is_dnd_active:
            self.logger.info(f"🔕 [Active] 免打扰生效，跳过{log_label}: {alert_app_name}")
            return True

        self.logger.info(
            f"⏰ [Active] {log_label}: {alert_app_name} ({active_minutes} min)"
        )
        self.add_observation(
            f"{log_label}：连续活跃 {active_minutes} 分钟",
            full_title,
            category,
            app_name=app_name,
            reason="sedentary",
            source=source,
        )
        self._show_sedentary_popup(alert_app_name, active_minutes)
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.chat_service.send_active_alert(alert_app_name, active_minutes),
                self._loop,
            )
        return True

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
        self.use_rust_events_only = True
        self._loop = loop
        if self.running and self._thread and self._thread.is_alive():
            self.logger.info("[ScreenSensor] monitor thread already running; reused")
            return
        self.restore_recent_work_session()
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self.logger.info("👀 [ScreenSensor] 启动完成 (含每日总结 + 视觉查岗)")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    # ==================== 鏁版嵁鎺ュ彛 ====================
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
        lines.append(f"[今日屏幕活动统计] 活跃时长: {total_hours:.1f} 小时")

        category_totals = self._compute_category_totals()
        if category_totals:
            lines.append("[按分类]")
            for cat, seconds in sorted(
                category_totals.items(), key=lambda x: x[1], reverse=True
            ):
                if seconds <= 0:
                    continue
                lines.append(f"- {cat}: {self._format_duration(seconds)}")

        lines.append("[按应用]")
        for app, duration_sec in sorted_apps:
            count = self.daily_counts.get(app, 0)
            time_str = self._format_duration(duration_sec)
            lines.append(f"- {app}: {time_str} ({count} 次)")

        obs_lines = self._format_compact_observations(
            self.observation_entries, limit=10
        )
        if obs_lines:
            lines.append("[今日观察摘要]")
            lines.extend(obs_lines)

        if self.activity_segments:
            lines.append("[最近活动切片]")
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
            lines.append("[Live2D 活动事件]")
            lines.extend(rust_lines)

        return "\n".join(lines)

    def _recent_rust_events(self, limit: int = 120) -> List[Dict[str, Any]]:
        if not get_memory_store:
            return []
        try:
            store = get_memory_store()
            if not store:
                return []
            events: List[Dict[str, Any]] = []
            if hasattr(store, "get_latest_activity_event"):
                latest = store.get_latest_activity_event(source=LIVE2D_ACTIVITY_SOURCE)
                if latest:
                    events.append(latest)
            events.extend(store.list_activity_events(
                limit=limit, source=LIVE2D_ACTIVITY_SOURCE
            ))
            deduped: List[Dict[str, Any]] = []
            seen_ids = set()
            for item in events:
                event_id = str(item.get("event_id") or "").strip()
                if event_id and event_id in seen_ids:
                    continue
                if event_id:
                    seen_ids.add(event_id)
                deduped.append(item)
            events = deduped
            events.sort(
                key=lambda item: self._parse_rust_event_ts(item), reverse=True
            )
            return events[: max(1, int(limit))]
        except Exception:
            return []

    def _mark_rust_event_processed(self, event_id: str, event_ts: float) -> None:
        event_id = str(event_id or "").strip()
        if event_id:
            self._last_rust_event_id = event_id
        if event_ts > 0:
            self._last_rust_processed_ts = max(
                float(getattr(self, "_last_rust_processed_ts", 0.0) or 0.0),
                event_ts,
            )

    def _should_use_rust_events_now(
        self, *, now_ts: float, stale_threshold_sec: float
    ) -> bool:
        recent_events = self._recent_rust_events(limit=1)
        newest_ts = (
            self._parse_rust_event_ts(recent_events[0]) if recent_events else 0.0
        )
        if recent_events and newest_ts > 0:
            self._last_rust_event_seen_at = newest_ts
        elif not getattr(self, "use_rust_events_only", False):
            return False

        last_seen = self._last_rust_event_seen_at
        if not last_seen or now_ts - last_seen > stale_threshold_sec:
            return False

        if not getattr(self, "use_rust_events_only", False):
            self.logger.info("[Screen] Rust activity events resumed; using Rust events")
            self.use_rust_events_only = True
        self._set_rust_activity_health(
            state="healthy",
            now_ts=now_ts,
            stale_threshold_sec=stale_threshold_sec,
        )
        return True

    def _set_rust_activity_health(
        self, *, state: str, now_ts: float, stale_threshold_sec: float
    ) -> None:
        previous = str(getattr(self, "_rust_health_state", "") or "")
        self._rust_health_state = state
        last_seen = float(getattr(self, "_last_rust_event_seen_at", 0.0) or 0.0)
        summary = (
            "Rust 活动事件正常" if state == "healthy" else "Rust 活动事件已过期"
        )
        try:
            self.runtime_health.report(
                "rust_activity",
                state,
                summary,
                details={
                    "source": LIVE2D_ACTIVITY_SOURCE,
                    "last_event_at": last_seen or None,
                    "stale_for_seconds": (
                        max(0.0, now_ts - last_seen) if last_seen else None
                    ),
                },
                stale_after_seconds=stale_threshold_sec,
                updated_at=now_ts,
            )
        except Exception:
            pass
        if previous == state:
            return
        if state == "healthy" and previous:
            self.logger.info("[Screen] Live2D activity events resumed")
        elif state == "degraded":
            self.logger.warning(
                "[Screen] Live2D activity events stale; waiting for live2d-tauri source"
            )

    def _warn_rust_events_stale(
        self, now_ts: float, stale_threshold_sec: float
    ) -> None:
        self._set_rust_activity_health(
            state="degraded",
            now_ts=now_ts,
            stale_threshold_sec=stale_threshold_sec,
        )

    def _is_ignored_rust_screen_event(
        self, *, app_name: str, title: str, kind: str
    ) -> bool:
        text = f"{app_name} {title}".strip().lower()
        if not text:
            return True
        if kind not in {"foreground_changed", "activity_sample"}:
            return False
        ignored_exact = {
            "none",
            "unknown",
            "program manager",
            "no foreground window (idle)",
        }
        app_key = str(app_name or "").strip().lower()
        title_key = str(title or "").strip().lower()
        if app_key in ignored_exact or title_key in ignored_exact:
            return True
        ignored_markers = (
            "no foreground window",
            "foreground window (idle)",
        )
        return any(marker in text for marker in ignored_markers)

    def _handle_rust_sedentary_alert(
        self,
        *,
        now_ts: float,
        event_ts: float,
        app_name: str,
        category: str,
        full_title: str,
        payload: Dict[str, Any],
    ) -> None:
        try:
            active_minutes = int(payload.get("active_minutes") or 0)
        except Exception:
            active_minutes = 0
        if active_minutes <= 0:
            return
        alert_app_name = SEDENTARY_SESSION_APP_NAME
        self._trigger_sedentary_alert(
            now_ts=now_ts,
            alert_app_name=alert_app_name,
            active_minutes=active_minutes,
            app_name=app_name,
            category=category,
            full_title=full_title,
            source=LIVE2D_ACTIVITY_SOURCE,
            log_label="Rust 久坐事件触发弹窗",
        )

    def _maybe_trigger_rust_sample_sedentary_alert(
        self,
        *,
        now_ts: float,
        event_ts: float,
        app_name: str,
        category: str,
        full_title: str,
        payload: Dict[str, Any],
    ) -> bool:
        if self.sedentary_interval_sec <= 0:
            return False
        if self._rust_payload_confirms_sedentary_break(payload):
            return False

        try:
            payload_minutes = int(payload.get("active_minutes") or 0)
        except Exception:
            payload_minutes = 0
        if not self._rust_sedentary_payload_is_trustworthy(payload_minutes):
            payload_minutes = 0

        if now_ts < self.next_sedentary_alert_time and (
            payload_minutes * 60 < self.sedentary_interval_sec
        ):
            return False

        active_minutes = payload_minutes
        if active_minutes <= 0:
            return False

        return self._trigger_sedentary_alert(
            now_ts=now_ts,
            alert_app_name=SEDENTARY_SESSION_APP_NAME,
            active_minutes=active_minutes,
            app_name=app_name,
            category=category,
            full_title=full_title,
            source=LIVE2D_ACTIVITY_SOURCE,
            log_label="Live2D sedentary payload alert",
        )

    def _sedentary_alert_minutes(self, now_ts: Optional[float] = None) -> int:
        session = self.get_current_work_session(now_ts=now_ts)
        try:
            active_minutes = int(session.get("active_minutes") or 0)
        except Exception:
            active_minutes = 0
        if active_minutes > 0:
            return active_minutes
        try:
            active_seconds = int(session.get("active_seconds") or 0)
        except Exception:
            active_seconds = 0
        if active_seconds > 0:
            return max(1, int(active_seconds // 60))
        return 0

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
        newest_ts = self._parse_rust_event_ts(events[0])
        if newest_ts > 0:
            self._last_rust_event_seen_at = newest_ts
        if not newest_id:
            return
        last_processed_ts = float(
            getattr(self, "_last_rust_processed_ts", 0.0) or 0.0
        )
        if newest_id == self._last_rust_event_id and (
            newest_ts <= 0 or newest_ts <= last_processed_ts
        ):
            return

        pending = []
        for item in events:
            event_id = str(item.get("event_id") or "").strip()
            if not event_id:
                continue
            if event_id == self._last_rust_event_id:
                break
            event_ts = self._parse_rust_event_ts(item)
            if event_ts > 0 and last_processed_ts > 0 and event_ts <= last_processed_ts:
                continue
            pending.append(item)
        pending.sort(key=lambda item: self._parse_rust_event_ts(item) or 0.0)

        if not pending:
            self._mark_rust_event_processed(newest_id, newest_ts)
            return

        # One pending batch may contain several switches. Keep state updates
        # chronological, but only comment on the final focus candidate.
        latest_switch_reaction: Optional[Dict[str, Any]] = None
        latest_duration_reaction: Optional[Dict[str, Any]] = None

        for latest in pending:
            event_id = str(latest.get("event_id") or "").strip()
            kind = str(latest.get("kind") or "").strip().lower()
            app = (
                str(((latest.get("app") or {}).get("name") or "")).strip() or "unknown"
            )
            event_ts = self._parse_rust_event_ts(latest) or now_ts
            title = str(latest.get("window_title") or "").strip()
            domain = str((((latest.get("browser") or {}).get("domain")) or "")).strip()
            full_title = title or domain or app
            if self._is_ignored_rust_screen_event(
                app_name=app,
                title=full_title,
                kind=kind,
            ):
                self._mark_rust_event_processed(event_id, event_ts)
                continue
            cat, app_name = self._analyze_window_context(
                app=app, title=title, domain=domain
            )
            self._remember_app_category(app_name, cat)
            payload = latest.get("sedentary") if isinstance(latest.get("sedentary"), dict) else {}
            payload_minutes = 0
            active_seconds = 0
            if self._rust_payload_confirms_sedentary_break(payload):
                self._reset_sedentary_session(
                    reset_ts=event_ts,
                    source=LIVE2D_ACTIVITY_SOURCE,
                    app_name=app_name,
                    category=cat,
                )
            else:
                try:
                    payload_minutes = (
                        int(payload.get("active_minutes") or 0)
                        if isinstance(payload, dict)
                        else 0
                    )
                except Exception:
                    payload_minutes = 0
                if not self._rust_sedentary_payload_is_trustworthy(payload_minutes):
                    payload_minutes = 0
                active_seconds = payload_minutes * 60
            if payload_minutes > 0:
                self._ensure_sedentary_session(
                    start_ts=max(0.0, event_ts - active_seconds),
                    source=LIVE2D_SEDENTARY_SOURCE,
                    app_name=app_name,
                    category=cat,
                    replace=True,
                )
            if kind == "sedentary_alert":
                self._handle_rust_sedentary_alert(
                    now_ts=now_ts,
                    event_ts=event_ts,
                    app_name=app_name,
                    category=cat,
                    full_title=full_title,
                    payload=payload,
                )
                self._mark_rust_event_processed(event_id, event_ts)
                continue

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
                previous_app_name = str(getattr(self, "last_app_name", "") or "").strip()
                app_switched = not previous_app_name or previous_app_name != app_name
                if app_switched:
                    self.daily_counts[app_name] = self.daily_counts.get(app_name, 0) + 1
                self.last_window_title = full_title
                self.last_app_name = app_name
                self.last_category = cat
                if app_switched:
                    self.current_window_start_time = event_ts
                    self.next_duration_trigger_time = event_ts + (20 * 60)
                    self._last_alert_app = None
                    # A newer switch invalidates any pending duration comment.
                    latest_duration_reaction = None
                    latest_switch_reaction = {
                        "full_title": full_title,
                        "category": cat,
                        "count": self.daily_counts.get(app_name, 1),
                        "app_name": app_name,
                        "reason": "switch",
                        "app_duration_sec": self.daily_durations.get(app_name, 0.0),
                        "current_stay_sec": 0.0,
                    }
                self._last_rust_sample_ts = event_ts
            elif kind == "activity_sample":
                if not str(getattr(self, "last_app_name", "") or "").strip():
                    self.last_window_title = full_title
                    self.last_app_name = app_name
                    self.last_category = cat
                    self.current_window_start_time = event_ts
                    self.next_duration_trigger_time = event_ts + (20 * 60)
                    self._last_rust_sample_ts = event_ts
                    self._mark_rust_event_processed(event_id, event_ts)
                    continue
                current_focus_app = str(getattr(self, "last_app_name", "") or "").strip()
                sample_matches_focus = (
                    not current_focus_app or current_focus_app == app_name
                )
                sample_seconds = self._rust_sample_seconds(event_ts, now_ts)
                if sample_seconds > 0 and sample_matches_focus:
                    self.daily_durations[app_name] = self.daily_durations.get(
                        app_name, 0.0
                    ) + sample_seconds
                if sample_matches_focus:
                    self.last_window_title = full_title
                    self.last_app_name = app_name
                    self.last_category = cat
                    self._last_rust_sample_ts = event_ts
                stay_minutes = max(
                    0, int((event_ts - self.current_window_start_time) / 60)
                )
                if (
                    sample_matches_focus
                    and event_ts >= self.next_duration_trigger_time
                ):
                    self.next_duration_trigger_time = event_ts + (20 * 60)
                    latest_duration_reaction = {
                        "full_title": full_title,
                        "category": cat,
                        "count": self.daily_counts.get(app_name, 1),
                        "app_name": app_name,
                        "reason": "duration",
                        "app_duration_sec": self.daily_durations.get(app_name, 0.0),
                        "current_stay_sec": max(
                            0.0, event_ts - self.current_window_start_time
                        ),
                        "stay_minutes": stay_minutes,
                    }

            self._mark_rust_event_processed(event_id, event_ts)

        reaction = latest_switch_reaction or latest_duration_reaction
        if not reaction:
            return
        reason = str(reaction.get("reason") or "switch")
        if reason == "switch":
            self._debug_log("📡 [Screen] Rust 事件按切屏逻辑尝试触发吐槽")
        else:
            stay_minutes = int(reaction.get("stay_minutes") or 0)
            self._debug_log(
                f"📡 [Screen] Rust 事件按停留逻辑尝试触发吐槽: stay={stay_minutes}min"
            )
        self._try_trigger_reaction(
            str(reaction.get("full_title") or ""),
            str(reaction.get("category") or "other"),
            int(reaction.get("count") or 1),
            str(reaction.get("app_name") or ""),
            reason=reason,
            app_duration_sec=float(reaction.get("app_duration_sec") or 0.0),
            current_stay_sec=float(reaction.get("current_stay_sec") or 0.0),
        )

    def _format_duration(self, seconds: float) -> str:
        seconds = float(seconds or 0.0)
        if seconds < 60:
            return f"{int(seconds)} 秒"
        if seconds < 3600:
            return f"{int(seconds / 60)} 分钟"
        return f"{seconds / 3600:.1f} 小时"

    def _rust_event_is_active(self, item: Dict[str, Any]) -> bool:
        presence = str(item.get("presence") or "").strip().lower()
        if presence and presence != "active":
            return False
        kind = str(item.get("kind") or "").strip().lower()
        if any(marker in kind for marker in ("idle", "away", "locked", "sleep")):
            return False
        return True

    def _describe_window_for_work_session(
        self, *, app: str = "", title: str = "", domain: str = ""
    ) -> Tuple[str, str]:
        app = str(app or "").strip()
        title = str(title or "").strip()
        domain = str(domain or "").strip()
        cache_key = f"app={app}|title={title}|domain={domain}"
        if cache_key in self.app_cache:
            cached = self.app_cache.get(cache_key) or []
            if len(cached) >= 2:
                return str(cached[1] or "other"), str(cached[0] or app or title or domain)

        match = self.app_registry.match(app=app, title=title, domain=domain)
        if match:
            rule = match.rule
            # Keep real process/title identity; do not collapse stats into category labels.
            app_name = self._resolve_app_display_name(
                app=app,
                title=title,
                domain=domain,
                fallback=rule.display_name,
            )
            return rule.category, app_name

        if app and app in self.app_category_map:
            return str(self.app_category_map.get(app) or "other"), app

        display_name = self._resolve_app_display_name(
            app=app, title=title, domain=domain
        )
        return "other", display_name

    def _rust_payload_confirms_sedentary_break(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            rest_streak = int(payload.get("rest_streak") or 0)
            break_minutes = int(
                payload.get("break_minutes")
                or getattr(config, "SEDENTARY_BREAK_MINUTES", 5)
                or 5
            )
        except Exception:
            return False
        return rest_streak >= max(1, break_minutes)

    def _rust_payload_looks_restarted(
        self, *, events: List[Dict[str, Any]], newest_ts: float, newest_minutes: int
    ) -> bool:
        newest_source = (
            str((events[0] or {}).get("source") or LIVE2D_ACTIVITY_SOURCE).strip()
            or LIVE2D_ACTIVITY_SOURCE
        )
        bridge_sec = float(WORK_SESSION_RESTART_BRIDGE_SEC)
        for item in events[1:]:
            item_source = (
                str(item.get("source") or LIVE2D_ACTIVITY_SOURCE).strip()
                or LIVE2D_ACTIVITY_SOURCE
            )
            if item_source != newest_source:
                continue
            event_ts = self._parse_rust_event_ts(item)
            if event_ts <= 0:
                continue
            if newest_ts - event_ts > bridge_sec:
                break
            payload = item.get("sedentary")
            if self._rust_payload_confirms_sedentary_break(payload):
                return False
            if not isinstance(payload, dict):
                continue
            try:
                previous_minutes = int(payload.get("active_minutes") or 0)
            except Exception:
                previous_minutes = 0
            if previous_minutes > newest_minutes + 1:
                return True
        return False

    def _rust_sedentary_payload_is_trustworthy(self, active_minutes: int) -> bool:
        return 0 < active_minutes <= WORK_SESSION_MAX_RUST_PAYLOAD_MINUTES

    def _sedentary_break_seconds(self) -> float:
        try:
            return max(
                60.0,
                float(getattr(config, "SEDENTARY_BREAK_MINUTES", 5))
                * 60.0,
            )
        except Exception:
            return 5 * 60.0

    def _empty_sedentary_session(
        self, *, source: str, app_name: str = "", category: str = "", state: str = ""
    ) -> Dict[str, Any]:
        session = {
            "active_seconds": 0,
            "active_minutes": 0,
            "app_name": str(app_name or SEDENTARY_SESSION_APP_NAME),
            "category": str(category or SEDENTARY_SESSION_CATEGORY),
            "source": source,
        }
        state = str(state or "").strip()
        if state:
            session["state"] = state
        return session

    def _reset_sedentary_session(
        self, *, reset_ts: float, source: str, app_name: str = "", category: str = ""
    ) -> None:
        self.sedentary_session_start_ts = 0.0
        self.sedentary_session_source = source
        self.sedentary_session_app_name = str(app_name or SEDENTARY_SESSION_APP_NAME)
        self.sedentary_session_category = str(category or SEDENTARY_SESSION_CATEGORY)

    def _ensure_sedentary_session(
        self,
        *,
        start_ts: float,
        source: str,
        app_name: str = "",
        category: str = "",
        replace: bool = False,
    ) -> None:
        start_ts = float(start_ts or time.time())
        current_start = float(getattr(self, "sedentary_session_start_ts", 0.0) or 0.0)
        if replace or current_start <= 0 or start_ts < current_start:
            self.sedentary_session_start_ts = start_ts
        self.sedentary_session_source = source
        self.sedentary_session_app_name = str(app_name or SEDENTARY_SESSION_APP_NAME)
        self.sedentary_session_category = str(category or SEDENTARY_SESSION_CATEGORY)

    def _build_sedentary_session(
        self,
        *,
        now_ts: float,
        source: str,
        app_name: str = "",
        category: str = "",
    ) -> Dict[str, Any]:
        start_ts = float(getattr(self, "sedentary_session_start_ts", 0.0) or 0.0)
        if start_ts <= 0:
            active_seconds = 0.0
        else:
            active_seconds = max(0.0, float(now_ts) - start_ts)
        return {
            "active_seconds": int(active_seconds),
            "active_minutes": int(active_seconds // 60),
            "app_name": str(
                app_name
                or getattr(self, "sedentary_session_app_name", "")
                or SEDENTARY_SESSION_APP_NAME
            ),
            "category": str(
                category
                or getattr(self, "sedentary_session_category", "")
                or SEDENTARY_SESSION_CATEGORY
            ),
            "source": source,
        }

    def _current_sedentary_session_from_events(
        self, now_ts: float, events: List[Dict[str, Any]], source: str
    ) -> Optional[Dict[str, Any]]:
        live2d_events = [
            item
            for item in events
            if str(item.get("source") or "").strip() == LIVE2D_ACTIVITY_SOURCE
        ]
        if not live2d_events:
            return None

        newest = live2d_events[0]
        newest_ts = self._parse_rust_event_ts(newest)
        max_resume_gap_sec = max(60.0, float(SCREEN_SENSOR_INTERVAL) * 60.0)
        if newest_ts <= 0 or now_ts - newest_ts > max_resume_gap_sec:
            self._reset_sedentary_session(
                reset_ts=now_ts,
                source=LIVE2D_ACTIVITY_SOURCE,
                app_name=SEDENTARY_SESSION_APP_NAME,
                category=SEDENTARY_SESSION_CATEGORY,
            )
            return None

        app_name = SEDENTARY_SESSION_APP_NAME
        cat = SEDENTARY_SESSION_CATEGORY
        sedentary_payload = newest.get("sedentary")
        if self._rust_payload_confirms_sedentary_break(sedentary_payload):
            self._reset_sedentary_session(
                reset_ts=newest_ts,
                source=LIVE2D_ACTIVITY_SOURCE,
                app_name=app_name,
                category=cat,
            )
            return self._empty_sedentary_session(
                source=LIVE2D_ACTIVITY_SOURCE,
                app_name=app_name,
                category=cat,
                state="resting",
            )

        try:
            active_minutes = int((sedentary_payload or {}).get("active_minutes") or 0)
        except Exception:
            active_minutes = 0
        if not self._rust_sedentary_payload_is_trustworthy(active_minutes):
            self._reset_sedentary_session(
                reset_ts=newest_ts,
                source=LIVE2D_ACTIVITY_SOURCE,
                app_name=app_name,
                category=cat,
            )
            return self._empty_sedentary_session(
                source=LIVE2D_ACTIVITY_SOURCE,
                app_name=app_name,
                category=cat,
            )

        active_seconds = active_minutes * 60
        self._ensure_sedentary_session(
            start_ts=max(0.0, now_ts - active_seconds),
            source=LIVE2D_SEDENTARY_SOURCE,
            app_name=app_name,
            category=cat,
            replace=True,
        )
        return {
            "active_seconds": active_seconds,
            "active_minutes": active_minutes,
            "app_name": app_name,
            "category": cat,
            "source": LIVE2D_SEDENTARY_SOURCE,
        }

    def _current_rust_sedentary_session(
        self, now_ts: float
    ) -> Optional[Dict[str, Any]]:
        return self._current_sedentary_session_from_events(
            now_ts,
            self._recent_rust_events(limit=WORK_SESSION_EVENT_LIMIT),
            LIVE2D_ACTIVITY_SOURCE,
        )

    def _latest_live2d_payload_session(
        self, now_ts: float
    ) -> Optional[Dict[str, Any]]:
        events = self._recent_rust_events(limit=1)
        if not events:
            return None
        latest = events[0]
        if str(latest.get("source") or "").strip() != LIVE2D_ACTIVITY_SOURCE:
            return None
        event_ts = self._parse_rust_event_ts(latest)
        max_resume_gap_sec = max(60.0, float(SCREEN_SENSOR_INTERVAL) * 60.0)
        if event_ts <= 0 or now_ts - event_ts > max_resume_gap_sec:
            return None
        payload = latest.get("sedentary")
        if self._rust_payload_confirms_sedentary_break(payload):
            return None
        try:
            active_minutes = int((payload or {}).get("active_minutes") or 0)
        except Exception:
            active_minutes = 0
        if not self._rust_sedentary_payload_is_trustworthy(active_minutes):
            return None
        active_seconds = active_minutes * 60
        self._ensure_sedentary_session(
            start_ts=max(0.0, now_ts - active_seconds),
            source=LIVE2D_SEDENTARY_SOURCE,
            app_name=SEDENTARY_SESSION_APP_NAME,
            category=SEDENTARY_SESSION_CATEGORY,
            replace=True,
        )
        return {
            "active_seconds": active_seconds,
            "active_minutes": active_minutes,
            "app_name": SEDENTARY_SESSION_APP_NAME,
            "category": SEDENTARY_SESSION_CATEGORY,
            "source": LIVE2D_SEDENTARY_SOURCE,
        }

    def get_current_work_session(self, now_ts: Optional[float] = None) -> Dict[str, Any]:
        now = float(now_ts or time.time())
        rust_session = self._current_rust_sedentary_session(now)
        if rust_session:
            return rust_session

        latest_payload_session = self._latest_live2d_payload_session(now)
        if latest_payload_session:
            return latest_payload_session

        self._reset_sedentary_session(
            reset_ts=now,
            source=LIVE2D_ACTIVITY_SOURCE,
            app_name=SEDENTARY_SESSION_APP_NAME,
            category=SEDENTARY_SESSION_CATEGORY,
        )
        return self._empty_sedentary_session(
            source=LIVE2D_ACTIVITY_SOURCE,
            app_name=SEDENTARY_SESSION_APP_NAME,
            category=SEDENTARY_SESSION_CATEGORY,
        )

    def restore_recent_work_session(self, now_ts: Optional[float] = None) -> bool:
        now = float(now_ts or time.time())
        latest_events = self._recent_rust_events(limit=1)
        if latest_events:
            latest = latest_events[0]
            self._last_rust_event_id = str(latest.get("event_id") or "").strip()
            latest_ts = self._parse_rust_event_ts(latest)
            if latest_ts > 0:
                self._last_rust_event_seen_at = latest_ts
                self._last_rust_processed_ts = max(
                    float(getattr(self, "_last_rust_processed_ts", 0.0) or 0.0),
                    latest_ts,
                )
        session = self._current_rust_sedentary_session(now)
        return bool(session and session.get("source") == LIVE2D_SEDENTARY_SOURCE)

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
                if "绂诲紑" in app_name or "idle" in app_name.lower():
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
                or "褰撳墠绐楀彛"
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
        self._sanitize_stats()
        self._reconcile_daily_counts_with_rust_events()
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
                        "[ScreenSensor] Ignored legacy stats cache without date"
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
            self._sanitize_stats()
            self._reconcile_daily_counts_with_rust_events()
        except Exception:
            pass

    def _is_polluted_stats_key(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        lowered = text.lower()
        ignored_exact = {
            "none",
            "unknown",
            "system",
            "program manager",
            "no foreground window (idle)",
            "no foreground window (active)",
            "no foreground window (locked)",
            "python",
            "python.exe",
            "live2d",
            "live2d-suzu",
            "live2d agent",
        }
        if lowered in ignored_exact:
            return True
        ignored_markers = (
            "no foreground window",
            "python-screen-sensor",
            "app=python.exe",
            "live2d-suzu",
            "live2d agent",
            "restart confirm",
            "idle",
        )
        return any(marker in lowered or marker in text for marker in ignored_markers)

    def _sanitize_stats(self) -> None:
        self.daily_counts = {
            str(app): count
            for app, count in dict(self.daily_counts or {}).items()
            if not self._is_polluted_stats_key(app)
        }
        self.daily_durations = {
            str(app): seconds
            for app, seconds in dict(self.daily_durations or {}).items()
            if not self._is_polluted_stats_key(app)
        }

        cleaned_cache: Dict[str, List[str]] = {}
        for key, value in dict(self.app_cache or {}).items():
            display_name = ""
            if isinstance(value, list) and value:
                display_name = str(value[0] or "")
            if self._is_polluted_stats_key(key) or self._is_polluted_stats_key(display_name):
                continue
            cleaned_cache[str(key)] = value
        self.app_cache = cleaned_cache

        cleaned_categories: Dict[str, str] = {}
        for app, category in dict(self.app_category_map or {}).items():
            if self._is_polluted_stats_key(app):
                continue
            cleaned_categories[str(app)] = str(category or "other")
        self.app_category_map = cleaned_categories

        cleaned_observations = []
        for entry in list(self.observation_entries or []):
            if not isinstance(entry, dict):
                cleaned_observations.append(entry)
                continue
            app = entry.get("app") or entry.get("app_name") or ""
            title = entry.get("window_title") or entry.get("title") or ""
            source = entry.get("source") or ""
            if (
                self._is_polluted_stats_key(app)
                or self._is_polluted_stats_key(title)
                or self._is_polluted_stats_key(source)
            ):
                continue
            cleaned_observations.append(entry)
        self.observation_entries = cleaned_observations

        cleaned_segments = []
        for segment in list(self.activity_segments or []):
            if not isinstance(segment, dict):
                cleaned_segments.append(segment)
                continue
            app = segment.get("app") or segment.get("app_name") or ""
            if self._is_polluted_stats_key(app):
                continue
            cleaned_segments.append(segment)
        self.activity_segments = cleaned_segments

    def _save_stats_if_allowed(self) -> None:
        if getattr(self, "_suppress_stats_save", False):
            return
        self._save_stats()

    def _reconcile_daily_counts_with_rust_events(self) -> None:
        if not self.daily_counts or not get_memory_store:
            return
        try:
            store = get_memory_store()
        except Exception:
            return
        if not store or not hasattr(store, "list_activity_events"):
            return

        try:
            events = store.list_activity_events(
                limit=5000,
                date_str=self._stats_date_key(),
                source=LIVE2D_ACTIVITY_SOURCE,
            )
        except Exception:
            return
        if not events:
            return

        raw_counts: Dict[str, int] = {}
        previous_suppress = bool(getattr(self, "_suppress_stats_save", False))
        self._suppress_stats_save = True
        try:
            for item in events:
                kind = str(item.get("kind") or "").strip().lower()
                if kind != "foreground_changed":
                    continue
                app = str(((item.get("app") or {}).get("name") or "")).strip()
                title = str(item.get("window_title") or "").strip()
                domain = str((((item.get("browser") or {}).get("domain")) or "")).strip()
                full_title = title or domain or app
                if self._is_ignored_rust_screen_event(
                    app_name=app,
                    title=full_title,
                    kind=kind,
                ):
                    continue
                _category, app_name = self._analyze_window_context(
                    app=app, title=title, domain=domain
                )
                if self._is_polluted_stats_key(app_name):
                    continue
                raw_counts[app_name] = raw_counts.get(app_name, 0) + 1
        finally:
            self._suppress_stats_save = previous_suppress

        if not raw_counts:
            return

        reconciled: Dict[str, int] = {}
        for app, count in dict(self.daily_counts or {}).items():
            app_name = str(app)
            cap = raw_counts.get(app_name)
            if cap is None:
                continue
            try:
                current_count = int(count)
            except Exception:
                current_count = 0
            if current_count <= 0 or cap <= 0:
                continue
            reconciled[app_name] = min(current_count, cap)
        self.daily_counts = reconciled

    def _save_stats(self):
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            self._sanitize_stats()
            self._reconcile_daily_counts_with_rust_events()
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
        # Sync screen stats to SQLite.
        if not get_memory_store:
            return
        try:
            self._sanitize_stats()
            store = get_memory_store()
            if store:
                stats_date = self._stats_date_key()

                # 璁＄畻鎬绘椂闀?(灏忔椂)
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


    def _resolve_app_display_name(
        self,
        *,
        app: str = "",
        title: str = "",
        domain: str = "",
        fallback: str = "",
    ) -> str:
        app = str(app or "").strip()
        title = str(title or "").strip()
        domain = str(domain or "").strip()
        fallback = str(fallback or "").strip()
        category_labels = {
            "coding",
            "gaming",
            "video",
            "social",
            "work",
            "design",
            "browser",
            "other",
            "self",
            "unknown",
        }
        if app:
            return app
        if fallback and fallback.lower() not in category_labels:
            return fallback
        return title or domain or fallback or "unknown"

    def _analyze_window_context(self, *, app: str = "", title: str = "", domain: str = ""):
        app = str(app or "").strip()
        title = str(title or "").strip()
        domain = str(domain or "").strip()
        cache_key = f"app={app}|title={title}|domain={domain}"
        if cache_key in self.app_cache:
            cached = self.app_cache.get(cache_key) or []
            cached_app = str(cached[0] if len(cached) >= 1 else "")
            cached_cat = str(cached[1] if len(cached) >= 2 else "other") or "other"
            app_name = self._resolve_app_display_name(
                app=app, title=title, domain=domain, fallback=cached_app
            )
            # Refresh polluted historical labels like app_name="coding".
            if app_name != cached_app:
                self.app_cache[cache_key] = [app_name, cached_cat]
            return cached_cat, app_name

        match = self.app_registry.match(app=app, title=title, domain=domain)
        if match:
            rule = match.rule
            # Keep real process/title identity; do not collapse stats into category labels.
            app_name = self._resolve_app_display_name(
                app=app,
                title=title,
                domain=domain,
                fallback=rule.display_name,
            )
            self.app_cache[cache_key] = [app_name, rule.category]
            self._save_stats_if_allowed()
            self._debug_log(
                f"🧭 [Screen] 应用规则命中: {rule.name} app={app} title={title[:60]} domain={domain} cat={rule.category}"
            )
            return rule.category, app_name

        text = f"{title} {domain} {app}".strip()
        text_lower = text.lower()
        if "youtube" in text_lower:
            app_name = title.split(" - YouTube")[0].strip() if title else domain or app
            app_name = f"YouTube: {app_name}" if app_name else "YouTube"
            self.app_cache[cache_key] = [app_name, "video"]
            self._save_stats_if_allowed()
            return "video", app_name
        if "bilibili" in text_lower:
            app_name = title.split(" - Bilibili")[0].strip() if title else domain or app
            app_name = f"Bilibili: {app_name}" if app_name else "Bilibili"
            self.app_cache[cache_key] = [app_name, "video"]
            self._save_stats_if_allowed()
            return "video", app_name
        for self_t in SELF_WINDOW_TITLES:
            if self_t and self_t.lower() in text_lower:
                app_name = app or title or domain or "Live2D"
                self.app_cache[cache_key] = [app_name, "self"]
                self._save_stats_if_allowed()
                return "self", app_name

        app_name = app or title or domain or "unknown"
        self.app_cache[cache_key] = [app_name, "other"]
        self._save_stats_if_allowed()
        return "other", app_name

    def _check_daily_reset(self):
        today = self._today_key()
        if today != self.current_day:
            self.logger.info("📅 新的一天，开始结算昨日数据?..")

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
                    f"[Screen] Preserved stats for {previous_day}; waiting for diary archiver"
                )

            self.daily_counts.clear()
            self.daily_durations.clear()
            self.observation_entries = []
            self.activity_segments = []
            self._last_rust_sample_ts = 0.0
            self.current_day = today
            self._save_stats()

    def _monitor_loop(self):
        # Consume Live2D/Tauri activity events; no local window polling fallback.
        rust_started_at = time.time()
        self._last_rust_event_seen_at = self._last_rust_event_seen_at or rust_started_at
        stale_threshold_sec = max(90.0, float(SCREEN_SENSOR_INTERVAL) * 12.0)
        self.logger.info(
            "[Screen] Live2D activity source enabled; Python window polling disabled"
        )

        while self.running:
            try:
                time.sleep(2)
                self._check_daily_reset()
                now_ts = time.time()
                if self._should_use_rust_events_now(
                    now_ts=now_ts, stale_threshold_sec=stale_threshold_sec
                ):
                    self._process_rust_events_for_reaction(now_ts)
                else:
                    self._warn_rust_events_stale(now_ts, stale_threshold_sec)
                self._save_stats()
            except Exception as e:
                self.logger.error(f"[Screen] Live2D activity loop error: {e}")


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

        # 1. 基础冷却检查(全局防刷屏)
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

        # 2. 智能防刷屏(针对 switch 事件)
        # 这里的目的是：不要切得太快，而不是限制“不说话”
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

            # 只有极高频才进行概率静默
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
                        f"🧪 [ScreenDebug] 高频切换静默，跳过吐槽? app={app_name} count={count}"
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

        self.logger.info(f"👀 [Screen] 触发 ChatService: {app_name} | Vision: False")

        if self._loop and self._loop.is_running():
            if app_duration_sec is None:
                app_duration_sec = self.daily_durations.get(app_name, 0.0)
            if current_stay_sec is None:
                current_stay_sec = max(0.0, time.time() - self.current_window_start_time)
            future = asyncio.run_coroutine_threadsafe(
                self.chat_service.handle_sensor_event(
                    full_title,
                    category,
                    count,
                    app_name=app_name,
                    reason=reason,
                    app_duration_sec=app_duration_sec,
                    current_stay_sec=current_stay_sec,
                ),
                self._loop,
            )
            future.add_done_callback(
                lambda fut: self._mark_reaction_if_sent(
                    fut,
                    reaction_time=now,
                    category=category,
                    app_name=app_name,
                    reason=reason,
                )
            )

    def _mark_reaction_if_sent(
        self,
        future: Any,
        *,
        reaction_time: float,
        category: str,
        app_name: str = "",
        reason: str = "",
    ) -> None:
        try:
            sent = bool(future.result())
        except Exception as exc:
            self.logger.error(f"ScreenSensor reaction task failed: {exc}")
            return
        if not sent:
            self._debug_log(
                f"🧪 [ScreenDebug] ChatService 未实际吐槽，不进入冷却? app={app_name} cat={category} reason={reason}"
            )
            return
        self.last_reaction_time = reaction_time
        self.category_reaction_times[category] = reaction_time
