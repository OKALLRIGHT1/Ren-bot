# modules/music_sensor.py
import asyncio
import threading
import time
from typing import Optional

# 尝试导入 winsdk
try:
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

    WINSDK_AVAILABLE = True
except ImportError:
    WINSDK_AVAILABLE = False

from core.logger import get_logger

# 引入配置
try:
    from config import MUSIC_APP_WHITELIST
except ImportError:
    # 默认白名单
    MUSIC_APP_WHITELIST = ["CloudMusic", "QQMusic", "Spotify", "foobar2000", "AppleMusic"]


class MusicSensor:
    def __init__(self, chat_service):
        self.chat_service = chat_service
        self.logger = get_logger()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._loop = None

        # 状态记录
        self.current_title = ""
        self.current_artist = ""
        self.is_playing = False
        self.last_comment_time = 0

    def start(self, loop):
        if not WINSDK_AVAILABLE:
            self.logger.warning("⚠️ 未安装 winsdk 或非 Windows 系统，音乐感知功能不可用")
            return

        self._loop = loop
        self.running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self.logger.info("🎵 [MusicSensor] 音乐感知模块已启动 (白名单过滤版)")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    async def _get_media_info(self):
        """获取当前媒体信息 (带应用过滤)"""
        try:
            manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            session = manager.get_current_session()

            if session:
                # [关键] 获取来源应用的 ID
                # 例如: "Netease.CloudMusic_..." 或 "Spotify.exe"
                app_id = session.source_app_user_model_id.lower()

                # 检查白名单
                is_music_app = False
                for allowed in MUSIC_APP_WHITELIST:
                    if allowed.lower() in app_id:
                        is_music_app = True
                        break

                # 如果不是白名单里的应用（比如是 Chrome 或 PotPlayer），直接忽略
                if not is_music_app:
                    # self.logger.debug(f"忽略非音乐应用媒体: {app_id}")
                    return None

                # 获取播放信息
                info = await session.try_get_media_properties_async()
                playback_info = session.get_playback_info()

                # 状态: 4=Playing, 5=Paused
                status = playback_info.playback_status
                is_playing = (status == 4)

                return {
                    "title": info.title,
                    "artist": info.artist,
                    "playing": is_playing,
                    "app_id": app_id
                }
        except Exception:
            pass
        return None

    def _monitor_loop(self):
        """后台监控循环"""
        # 创建一个新的 event loop 用于 winsdk 的异步调用
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self.running:
            try:
                time.sleep(3)  # 每3秒检查一次

                info = loop.run_until_complete(self._get_media_info())

                # 如果 info 为 None (没播放，或者是视频软件)，视为不播放
                if not info:
                    if self.is_playing:
                        self.is_playing = False  # 状态复位
                    continue

                new_title = info["title"]
                new_artist = info["artist"]
                new_playing = info["playing"]
                app_id = info["app_id"]

                # 状态发生变化时触发
                if (new_title != self.current_title or
                        new_playing != self.is_playing):

                    self.current_title = new_title
                    self.current_artist = new_artist
                    self.is_playing = new_playing

                    if self.is_playing and new_title:
                        self.logger.info(f"🎵 识别到音乐软件 ({app_id}): {new_title}")
                        self._trigger_music_event(new_title, new_artist)

            except Exception as e:
                self.logger.error(f"MusicSensor error: {e}")
                time.sleep(5)

        loop.close()

    def _trigger_music_event(self, title, artist):
        """触发音乐事件"""
        now = time.time()
        if now - self.last_comment_time < 300:  # 5分钟冷却
            return

        self.logger.info(f"🎵 [Music] 触发评论: {title} - {artist}")

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.chat_service.handle_music_event(title, artist),
                self._loop
            )
        self.last_comment_time = now