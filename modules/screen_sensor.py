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
from typing import Optional, Dict, Tuple, List

import config
from modules.llm import chat_with_ai

from config import (
    SCREEN_SENSOR_ENABLED, SCREEN_SENSOR_INTERVAL,
    WINDOW_CATEGORIES, WINDOW_IGNORE_KEYWORDS, SCREEN_SMART_DEBOUNCE,
    SCREEN_REACTION_COOLDOWN, SCREEN_GLOBAL_COOLDOWN, SELF_WINDOW_TITLES
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
    if os.name == 'nt':
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
        self.current_day = datetime.now().day

        self._load_stats()
        self._last_alert_app = None
        self._last_alert_time = 0

    def start(self, loop):
        if not SCREEN_SENSOR_ENABLED:
            return
        if not _PYGETWINDOW_OK:
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
        """生成今日数据的格式化文本 (含时长)"""
        if not self.daily_counts:
            return "（今日尚无任何屏幕活动记录）"

        # 按时长倒序排列 (通常时长比次数更重要)
        # 如果你想按次数排，就把 x[1] 改成 self.daily_counts.get(x[0], 0)
        sorted_apps = sorted(self.daily_durations.items(), key=lambda x: x[1], reverse=True)

        lines = []

        # 计算总时长 (秒 -> 小时)
        total_seconds = sum(self.daily_durations.values())
        total_hours = total_seconds / 3600.0
        lines.append(f"【今日屏幕活动统计】(活跃时长: {total_hours:.1f}小时)")

        category_counts = {}

        for app, duration_sec in sorted_apps:
            count = self.daily_counts.get(app, 0)

            # 查缓存获取分类
            cat = "other"
            if app in self.app_cache:  # 你的代码里 app_cache key是title，这里逻辑可能要微调
                # 为了简单，我们可以重新根据 analyze 获取，或者在 _monitor_loop 里维护 category_map
                # 这里假设 app_cache 的结构能查到，或者直接展示 app
                pass

            # 格式化时长
            if duration_sec < 60:
                time_str = f"{int(duration_sec)}秒"
            elif duration_sec < 3600:
                time_str = f"{int(duration_sec / 60)}分钟"
            else:
                time_str = f"{duration_sec / 3600:.1f}小时"

            lines.append(f"- {app}: {time_str} ({count}次)")

        return "\n".join(lines)

    # ==================== 持久化 ====================
    def _load_stats(self):
        if not os.path.exists(self.stats_file): return
        try:
            with open(self.stats_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.app_cache = data.get("cache", {})
            if data.get("day") == datetime.now().day:
                self.daily_counts = data.get("counts", {})
                # 🟢 读取时长
                self.daily_durations = data.get("durations", {})
            else:
                self.daily_counts = {}
                self.daily_durations = {}
        except Exception:
            pass

    def _save_stats(self):
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            data = {
                "day": self.current_day,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "counts": self.daily_counts,
                # 🟢 保存时长
                "durations": self.daily_durations,
                "cache": self.app_cache
            }
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self._sync_to_db()
        except Exception:
            pass

    # ==================== 数据库同步 ====================
    def _sync_to_db(self):
        """将屏幕统计数据同步到 SQLite 数据库"""
        if not get_memory_store: return
        try:
            store = get_memory_store()
            if store:
                today_str = datetime.now().strftime("%Y-%m-%d")

                # 计算总时长 (小时)
                total_seconds = sum(self.daily_durations.values())
                total_hours = total_seconds / 3600.0

                # 构造数据包
                # 注意：memory_sqlite.py 的 save_daily_screen_stats 会从这个字典里提取 'total_hours'
                # 并将整个字典存入 'summary_json' 字段
                data_to_save = {
                    "summary_text": self.get_formatted_report(),  # 预生成的文本报告
                    "counts": self.daily_counts,  # 次数统计
                    "durations": self.daily_durations,  # 时长统计 (秒)
                    "total_hours": total_hours,  # 总时长 (小时)
                    "cache": self.app_cache,  # 分类缓存
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                # 调用 memory_sqlite.py 中的接口写入 daily_screen_stats 表
                store.save_daily_screen_stats(today_str, data_to_save)

                # self.logger.debug(f"💾 [Screen] 数据已同步至数据库: {today_str}")
        except Exception as e:
            self.logger.error(f"⚠️ 屏幕数据同步 DB 失败: {e}")

    # ==================== 核心逻辑 ====================
    def _get_active_window_info(self):
        """
        获取当前活动窗口，并检测是否为全屏状态
        返回: (窗口标题: str, 是否全屏: bool)
        """
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
                if os.name == 'nt':
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
            if not store: return ""

            # 使用 memory_sqlite.py 中定义的 get_profile 方法
            # 它会返回 {'name':..., 'likes':..., 'dislikes':..., 'notes':...}
            p = store.get_profile()

            likes = p.get("likes", [])
            # 兼容处理: 如果是新版字典结构 {'music':[], 'games':[]}，转为列表
            if isinstance(likes, dict):
                flat_likes = []
                for k, v in likes.items():
                    if isinstance(v, list): flat_likes.extend(v)
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
        today = datetime.now().day
        if today != self.current_day:
            self.logger.info("📅 新的一天，开始结算昨日数据...")

            if self.daily_counts and self._loop:
                raw_data = self.get_formatted_report()

                async def _do_summary(data_str):
                    await self.chat_service.summarize_day(data_str, auto=True)

                asyncio.run_coroutine_threadsafe(_do_summary(raw_data), self._loop)

            self.daily_counts.clear()
            self.current_day = today
            self._save_stats()

    def _monitor_loop(self):
        """后台监控循环 (终极版：时间轴修正 + 挂机检测 + 精准免打扰)"""
        import config  # 放到循环内或顶部均可

        # 如果初始化时没加，这里做个兜底防报错
        if not hasattr(self, 'is_afk'):
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
                    self.logger.info(f"💤 [Screen] 检测到系统休眠苏醒 (跳过 {elapsed:.1f}s)")
                    self.current_window_start_time = now
                    self.next_duration_trigger_time = now + self.DURATION_TRIGGER_THRESHOLD
                    elapsed = SCREEN_SENSOR_INTERVAL

                self._check_daily_reset()

                # 2. 获取窗口与全屏状态
                current_title, is_fullscreen = self._get_active_window_info()

                # 3. 锁屏/无窗口保护
                if not current_title:
                    self.current_window_start_time = now
                    self.next_duration_trigger_time = now + self.DURATION_TRIGGER_THRESHOLD
                    continue

                # 4. 分析分类
                cat, app = self._analyze_window(current_title)

                # 🟢 [核心修复] 精准免打扰逻辑：手动开启，或者 (处于全屏 且 必须是打游戏/看视频)
                is_dnd_active = getattr(config, 'DND_MODE', False) or (is_fullscreen and cat in ["gaming", "video"])

                # ========================================================
                # 5. 动态挂机检测
                # ========================================================
                if (is_fullscreen and cat in ["gaming", "video"]) or cat == "video":
                    current_afk_threshold = 7200  # 全屏游戏/视频，或普通视频：容忍 2 小时不动
                elif cat == "gaming":
                    current_afk_threshold = 1800  # 普通窗口游戏：容忍 30 分钟不动
                else:
                    current_afk_threshold = self.AFK_THRESHOLD_SEC  # 普通办公：容忍 5 分钟不动

                idle_sec = get_idle_duration()

                if idle_sec > current_afk_threshold:
                    if not self.is_afk:
                        self.logger.info(
                            f"🚶 [Screen] 离开电脑 (空闲 {int(idle_sec)}s，当前阈值 {current_afk_threshold}s)")
                        self.is_afk = True

                    self.current_window_start_time = now
                    self.next_duration_trigger_time = now + self.DURATION_TRIGGER_THRESHOLD
                    self.daily_durations["[离开电脑]"] = self.daily_durations.get("[离开电脑]", 0.0) + elapsed
                    self._save_stats()
                    continue

                elif self.is_afk:
                    self.logger.info("🏃 [Screen] 用户回来了！")
                    self.is_afk = False
                    self.current_window_start_time = now
                    self.next_duration_trigger_time = now + self.DURATION_TRIGGER_THRESHOLD
                # ========================================================

                # 6. 正常累加当前软件时长
                self.daily_durations[app] = self.daily_durations.get(app, 0.0) + elapsed

                is_switch = app != self.last_app_name

                if is_switch:
                    # ========== 场景A: 切换窗口 ==========
                    self.last_window_title = current_title
                    self.last_app_name = app
                    self.last_category = cat

                    self.current_window_start_time = now
                    self.next_duration_trigger_time = now + self.DURATION_TRIGGER_THRESHOLD
                    self._last_alert_app = None

                    self.daily_counts[app] = self.daily_counts.get(app, 0) + 1
                    self._save_stats()

                    # 🟢 使用统一的精准免打扰拦截
                    if is_dnd_active:
                        self.logger.info(f"🔕 [Screen] 免打扰生效，静默记录切换: {app}")
                    else:
                        count = self.daily_counts[app]
                        self._try_trigger_reaction(current_title, cat, count, app, reason="switch")

                else:
                    # ========== 场景B: 停留 ==========
                    self._save_stats()
                    stay_minutes = int((now - self.current_window_start_time) / 60)

                    # 久坐提醒
                    if stay_minutes >= 60 and stay_minutes % 60 == 0:
                        if self._last_alert_app != app or (now - self._last_alert_time) > 300:
                            if is_dnd_active:
                                self.logger.info(f"🔕 [Active] 免打扰生效，跳过久坐语音: {app}")
                                self._last_alert_app = app
                                self._last_alert_time = now
                            else:
                                self.logger.info(f"⏰ [Active] 触发久坐提醒: {app} ({stay_minutes} min)")
                                self._last_alert_app = app
                                self._last_alert_time = now
                                if self._loop:
                                    asyncio.run_coroutine_threadsafe(
                                        self.chat_service.send_active_alert(app, stay_minutes),
                                        self._loop
                                    )

                    # 沉浸时长吐槽
                    monitor_cats = ["gaming", "video", "coding", "work", "design"]
                    if cat in monitor_cats:
                        if now > self.next_duration_trigger_time:
                            self.next_duration_trigger_time = now + (30 * 60)
                            if is_dnd_active:
                                self.logger.info(f"🔕 [Screen] 免打扰生效，跳过沉浸查岗: <{app}>")
                            else:
                                self.logger.info(f"⏳ [Screen] 沉浸时长触发: <{app}>")
                                count = self.daily_counts.get(app, 1)
                                self._try_trigger_reaction(current_title, cat, count, app, reason="duration")

            except Exception as e:
                self.logger.error(f"ScreenSensor error: {e}")
                time.sleep(SCREEN_SENSOR_INTERVAL)
                last_tick_time = time.time()

    def _try_trigger_reaction(self, full_title: str, category: str, count: int, app_name: str, reason: str = "switch"):
        now = time.time()

        # 1. 基础冷却检查 (全局防刷屏)
        # 如果刚说完话不到 10 秒 (SCREEN_GLOBAL_COOLDOWN)，绝对闭嘴
        if now - self.last_reaction_time < SCREEN_GLOBAL_COOLDOWN:
            return

        # 2. 智能防刷屏 (针对 switch 事件)
        # 这里的目的是：不要切太快，而不是限制“不说话”
        if reason == "switch" and SCREEN_SMART_DEBOUNCE:
            cd = SCREEN_REACTION_COOLDOWN
            # 只有当频率极高时才增加冷却，平时尽量放行
            if count > 5: cd *= 2
            if count > 20: cd *= 4

            # 如果还在分类冷却期内，直接跳过 (这是为了防止 ChatService 压力过大)
            if now - self.category_reaction_times.get(category, 0) < cd:
                return

            # 只有极高频次才进行概率静音
            should_talk = True
            if count > 20 and count % 10 != 0: should_talk = False
            if not should_talk: return

        elif reason == "switch":
            if now - self.category_reaction_times.get(category, 0) < SCREEN_REACTION_COOLDOWN:
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
            interesting_cats = ["gaming", "video", "social", "design", "coding", "work", "other"]

            # 只有在这些分类下，才有概率“升级”为截图
            if category in interesting_cats:
                base_prob = 0.15
                prob_boost = count * 0.05
                final_prob = min(base_prob + prob_boost, 0.85)

                # 掷骰子！
                if random.random() < final_prob:
                    use_vision = True
                    self.logger.info(f"🎲 [Sensor] 运气爆棚！升级为视觉查岗 (概率: {final_prob:.2f})")
                else:
                    # 没摇中，仅作为普通文本事件处理
                    # self.logger.info(f"🎲 [Sensor] 只是普通观察 (未触发视觉)")
                    pass

        self.logger.info(f"👀 [Screen] 触发 ChatService: {app_name} | Vision: {use_vision}")

        # 4. 执行发送
        # 无论 use_vision 是 True 还是 False，都发送给 ChatService
        # - True  -> ChatService 调用 Smart Model (看图+吐槽)
        # - False -> ChatService 调用 Gatekeeper (判断是否无聊 -> 决定是否吐槽)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.chat_service.handle_sensor_event(full_title, category, count, use_vision=use_vision),
                self._loop
            )

        self.last_reaction_time = now
        self.category_reaction_times[category] = now
