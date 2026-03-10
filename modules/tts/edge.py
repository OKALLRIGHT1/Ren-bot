# modules/tts/edge.py
import asyncio
import os
import tempfile
from typing import Callable, Awaitable, Optional, Tuple

import edge_tts

try:
    import miniaudio
except Exception:
    miniaudio = None

from modules.live2d import play_sound_file, send_lip_sync
from core.logger import get_logger

logger = get_logger()
CACHE_DIR = os.path.abspath("./audio_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


class EdgeTTS:
    def __init__(
            self,
            voice: str,
            rate: str = "+0%",
            volume: str = "+0%",
            enabled: bool = True,
            max_chars: int = 250,
            use_live2d_player: bool = True,
            live2d_channel: int = 0,
            live2d_volume: float = 1.0,
            enable_lip_sync: bool = False,
            rhubarb_path: str = "./tools/rhubarb/rhubarb.exe",
            lip_sync_smooth_window: int = 3,
    ):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.enabled = bool(enabled)
        self.max_chars = int(max_chars)
        self.use_live2d_player = bool(use_live2d_player)
        self.live2d_channel = int(live2d_channel)
        self.live2d_volume = float(live2d_volume)
        self.enable_lip_sync = bool(enable_lip_sync)
        self.lip_sync_smooth_window = int(lip_sync_smooth_window)
        self.lip_sync_engine = None

        if self.enable_lip_sync:
            try:
                from modules.lip_sync import RhubarbLipSync
                self.lip_sync_engine = RhubarbLipSync(rhubarb_path=rhubarb_path)
                if not self.lip_sync_engine.is_available():
                    self.enable_lip_sync = False
            except Exception:
                self.enable_lip_sync = False

        self._lock = asyncio.Lock()

    def _clip(self, text: str) -> str:
        t = (text or "").strip()
        if not t: return ""
        if len(t) > self.max_chars:
            t = t[: self.max_chars].rstrip()
        return t

    def _estimate_duration_sec(self, mp3_path: str, text: str) -> float:
        if miniaudio is not None:
            try:
                decoded = miniaudio.decode_file(mp3_path, output_format=miniaudio.SampleFormat.FLOAT32)
                dur = len(decoded.samples) / 4.0 / max(1, decoded.nchannels) / max(1, decoded.sample_rate)
                return float(max(0.3, min(60.0, dur)))
            except:
                pass
        return max(1.2, min(25.0, len(text) / 5.0))

    # 🟢 [新增] 纯生成
    async def synthesize(self, text: str) -> Tuple[Optional[str], float]:
        if not self.enabled: return None, 0.0
        text = self._clip(text)
        if not text: return None, 0.0

        async with self._lock:
            fd, mp3_path = tempfile.mkstemp(suffix=".mp3", dir=CACHE_DIR)
            os.close(fd)
            mp3_path = os.path.abspath(mp3_path)

            try:
                print(f"🔊 [Edge] 合成: {text!r}")
                communicate = edge_tts.Communicate(text=text, voice=self.voice, rate=self.rate, volume=self.volume)
                await communicate.save(mp3_path)
                dur = self._estimate_duration_sec(mp3_path, text)
                return mp3_path, dur
            except Exception as e:
                print(f"❌ [Edge] 失败: {e}")
                if os.path.exists(mp3_path): os.remove(mp3_path)
                return None, 0.0

    # 🟢  纯播放 (增加延迟清理)
    async def play_audio_file(self, mp3_path: str, interrupt_event: asyncio.Event = None):
        if not mp3_path or not os.path.exists(mp3_path): return

        lip_sync_task = None
        if self.enable_lip_sync and self.lip_sync_engine:
            try:
                lip_sync_task = asyncio.create_task(self.lip_sync_engine.analyze_audio(mp3_path))
            except:
                pass

        if self.use_live2d_player:
            # 发送播放指令
            await play_sound_file(mp3_path, channel=self.live2d_channel, volume=self.live2d_volume)

            # 处理口型
            if lip_sync_task:
                try:
                    lip_data = await lip_sync_task
                    if lip_data:
                        lip_data = self.lip_sync_engine.smooth_lip_data(lip_data,
                                                                        window_size=self.lip_sync_smooth_window)
                        await send_lip_sync(self.lip_sync_engine.fade_lip_data(lip_data))
                except:
                    pass

        # 🟢 [修改] 延迟 10 秒删除，防止前端加载失败
        async def delayed_remove(path: str):
            await asyncio.sleep(10)
            try:
                if os.path.exists(path): os.remove(path)
            except:
                pass

        asyncio.create_task(delayed_remove(mp3_path))

    async def say(self, text: str, emotion: str | None = None, **kwargs) -> bool:
        path, dur = await self.synthesize(text)
        if path:
            await self.play_audio_file(path, kwargs.get("interrupt_event"))
            # 简单的阻塞以模拟等待播放
            await asyncio.sleep(dur)
            return True
        return False