# modules/tts/gptsovits.py
import os
import asyncio
import tempfile
import requests
import wave
from typing import Callable, Awaitable, Optional, Tuple

from modules.tts.base import BaseTTS
from modules.live2d import play_sound_file, send_lip_sync
from core.logger import get_logger

logger = get_logger()

try:
    from modules.tts.emotions import TTS_EMO_MAP
except Exception:
    TTS_EMO_MAP = {}

from config import GPT_W, SOV_W, REF_WAV, PROMPT_LANG, PROMPT_TEXT

try:
    from config import GPTSOVITS_BASE
except Exception:
    GPTSOVITS_BASE = "http://127.0.0.1:9880"


def _abs_exist(path: str, name: str) -> str:
    p = os.path.abspath(path or "")
    if not p or not os.path.isfile(p):
        raise FileNotFoundError(f"{name} 不存在: {p}")
    return p


def _estimate_wav_duration_sec(wav_path: str, text: str) -> float:
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return float(frames / max(1, rate))
    except Exception:
        return max(1.2, min(35.0, len(text) / 5.0))


# 缓存目录
CACHE_DIR = os.path.abspath("./audio_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


class GPTSoVITSTTS(BaseTTS):
    def __init__(
            self,
            base: str | None = None,
            timeout: int = 300,
            verbose: bool = True,
            enable_lip_sync: bool = False,
            rhubarb_path: str = "./tools/rhubarb/rhubarb.exe",
            lip_sync_smooth_window: int = 3,
    ):
        self.base = (base or GPTSOVITS_BASE or "http://127.0.0.1:9880").rstrip("/")
        self.timeout = int(timeout)
        self.verbose = bool(verbose)
        self.lock = asyncio.Lock()
        self.ready = False

        # 口型同步配置
        self.enable_lip_sync = bool(enable_lip_sync)
        self.lip_sync_smooth_window = int(lip_sync_smooth_window)
        self.lip_sync_engine = None

        if self.enable_lip_sync:
            try:
                from modules.lip_sync import RhubarbLipSync
                self.lip_sync_engine = RhubarbLipSync(rhubarb_path=rhubarb_path)
                if not self.lip_sync_engine.is_available():
                    self.enable_lip_sync = False
                else:
                    logger.info("GPT-SoVITS: Rhubarb 口型同步已启用")
            except Exception as e:
                self.enable_lip_sync = False

        self._init_model()

    def _req(self, path, params=None) -> requests.Response:
        r = requests.get(f"{self.base}{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r

    def _init_model(self):
        try:
            try:
                requests.get(f"{self.base}/", timeout=2)
            except Exception as e:
                raise ConnectionError(f"无法连接 GPT-SoVITS 服务: {self.base} ({e})")

            gpt_path = _abs_exist(GPT_W, "GPT_W")
            sov_path = _abs_exist(SOV_W, "SOV_W")
            _abs_exist(REF_WAV, "REF_WAV")

            self._req("/set_gpt_weights", {"weights_path": gpt_path})
            self._req("/set_sovits_weights", {"weights_path": sov_path})

            self.ready = True
            if self.verbose:
                print(f"✅ [GPT-SoVITS] 就绪 | base={self.base}")

        except Exception as e:
            self.ready = False
            if self.verbose:
                print(f"⚠️ [GPT-SoVITS] 不可用: {e}")

    def _pick_ref_prompt(self, emotion: str | None):
        emo = (emotion or "neutral").strip().lower()
        cfg = TTS_EMO_MAP.get(emo) or TTS_EMO_MAP.get("neutral") or {}
        ref = os.path.abspath(cfg.get("ref") or REF_WAV)
        prompt = cfg.get("prompt") or PROMPT_TEXT
        if not os.path.isfile(ref):
            ref = os.path.abspath(REF_WAV)
            prompt = PROMPT_TEXT
        return ref, prompt

    # 🟢 [新增] 只生成，不播放 (用于流水线)
    async def synthesize(self, text: str, emotion: str | None = None) -> Tuple[Optional[str], float]:
        """
        返回: (wav_abs_path, duration_sec)
        """
        if not self.ready: return None, 0.0
        text = (text or "").strip()
        if not text: return None, 0.0

        ref_abs, prompt = self._pick_ref_prompt(emotion)

        async with self.lock:
            fd, wav_path = tempfile.mkstemp(suffix=".wav", dir=CACHE_DIR)
            os.close(fd)
            wav_path = os.path.abspath(wav_path)

            try:
                params = {
                    "text": text,
                    "text_lang": "auto",
                    "ref_audio_path": ref_abs,
                    "prompt_text": prompt,
                    "prompt_lang": PROMPT_LANG,
                    "media_type": "wav",
                    "streaming_mode": "false",
                }

                r = await asyncio.to_thread(self._req, "/tts", params)
                ct = (r.headers.get("content-type") or "").lower()
                if ("audio" not in ct) and ("octet-stream" not in ct):
                    raise RuntimeError(f"/tts 返回非音频: ct={ct}")

                with open(wav_path, "wb") as f:
                    f.write(r.content)

                dur = _estimate_wav_duration_sec(wav_path, text)
                return wav_path, dur

            except Exception as e:
                self.ready = False
                if self.verbose:
                    print(f"⚠️ [GPT-SoVITS] synthesize() 失败: {e}")
                # 失败时尝试清理
                try:
                    if os.path.exists(wav_path): os.remove(wav_path)
                except:
                    pass
                return None, 0.0

    # 纯播放逻辑 (增加延迟清理)
    async def play_audio_file(self, wav_path: str, interrupt_event: asyncio.Event = None):
        if not wav_path or not os.path.exists(wav_path):
            return

        # 启动口型分析任务
        lip_sync_task = None
        if self.enable_lip_sync and self.lip_sync_engine:
            try:
                lip_sync_task = asyncio.create_task(
                    self.lip_sync_engine.analyze_audio(wav_path)
                )
            except Exception:
                pass

        # 播放指令 (通过 WS 发送给前端)
        await play_sound_file(wav_path)

        # 发送口型数据
        if lip_sync_task:
            try:
                lip_data = await lip_sync_task
                if lip_data:
                    lip_data = self.lip_sync_engine.smooth_lip_data(
                        lip_data, window_size=self.lip_sync_smooth_window
                    )
                    lip_data = self.lip_sync_engine.fade_lip_data(lip_data)
                    await send_lip_sync(lip_data)
            except Exception:
                pass

        # 🟢 [修改] 改为异步延迟删除，确保前端有足够时间加载音频
        async def delayed_remove(path: str):
            await asyncio.sleep(10)  # 等待 10 秒
            try:
                if os.path.exists(path):
                    os.remove(path)
            except:
                pass

        asyncio.create_task(delayed_remove(wav_path))

    # 兼容旧接口 (非流水线模式用)
    async def say(self, text: str, emotion: str | None = None, **kwargs) -> bool:
        path, dur = await self.synthesize(text, emotion)
        if path:
            await self.play_audio_file(path, kwargs.get("interrupt_event"))
            return True
        return False