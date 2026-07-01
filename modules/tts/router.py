# modules/tts/router.py
import asyncio
import re
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

from modules.tts.edge import EdgeTTS
from modules.state_machine import AgentStateMachine, AgentState
from modules.tts.stream_utils import StreamSentenceBuffer
from modules.live2d import stop_sound, estimate_bubble_display_ms  # 需要引入停止函数
from services.chat_support.text_splitter import split_chat_text_parts

try:
    from modules.llm import chat_with_ai
except ImportError:
    chat_with_ai = None

try:
    from modules.tts.gptsovits import GPTSoVITSTTS
except Exception as e:
    print(f"⚠️ [TTS] GPT-SoVITS 模块导入失败: {e}")
    GPTSoVITSTTS = None

try:
    from config import TTS_AUTO_TRANSLATE
except ImportError:
    TTS_AUTO_TRANSLATE = False


@dataclass
class SpeakItem:
    text: str
    emotion: Optional[str]
    interrupt: bool
    split_long: bool
    chunk_chars: int
    show_bubble: bool


@dataclass
class AudioItem:
    audio_path: str
    duration: float
    text_for_bubble: str  # 显示在气泡里的原文（中文）
    emotion: str
    backend: object  # 对应的 TTS 实例 (gpt 或 edge)
    tail_padding: float = 0.5


def _split_text(text: str, max_chars: int) -> list[str]:
    return split_chat_text_parts(text, max_len=max_chars)


class TTSRouter:
    def __init__(
        self,
        edge_cfg: dict,
        verbose: bool = True,
        log_each_utterance: bool = False,
        bubble_sender: Optional[
            Callable[[str, Optional[str], Optional[int]], Awaitable[None]]
        ] = None,
        go_idle_fn: Optional[Callable[[], Awaitable[None]]] = None,
        split_long_default: bool = True,
        chunk_chars_default: int = 90,
        state_machine: Optional[AgentStateMachine] = None,
        enable_lip_sync: Optional[bool] = None,
        rhubarb_path: Optional[str] = None,
        lip_sync_smooth_window: Optional[int] = None,
    ):
        self.edge = EdgeTTS(**edge_cfg)
        self.verbose = bool(verbose)
        self.log_each_utterance = bool(log_each_utterance)
        self.bubble_sender = bubble_sender
        self.go_idle_fn = go_idle_fn
        self.split_long_default = bool(split_long_default)
        self.chunk_chars_default = int(chunk_chars_default)
        self.segment_pause_sec = 0.18
        self.final_pause_sec = 0.50
        self.sm = state_machine

        cfg_enable_lip_sync = bool(edge_cfg.get("enable_lip_sync", False))
        cfg_rhubarb_path = edge_cfg.get("rhubarb_path", "./tools/rhubarb/rhubarb.exe")
        cfg_lip_sync_smooth_window = int(edge_cfg.get("lip_sync_smooth_window", 3))

        self.enable_lip_sync = (
            cfg_enable_lip_sync if enable_lip_sync is None else bool(enable_lip_sync)
        )
        self.rhubarb_path = cfg_rhubarb_path if rhubarb_path is None else rhubarb_path
        self.lip_sync_smooth_window = (
            cfg_lip_sync_smooth_window
            if lip_sync_smooth_window is None
            else int(lip_sync_smooth_window)
        )

        self.gpt = None
        self._active = "edge"

        if GPTSoVITSTTS is not None:
            try:
                self.gpt = GPTSoVITSTTS(
                    enable_lip_sync=self.enable_lip_sync,
                    rhubarb_path=self.rhubarb_path,
                    lip_sync_smooth_window=self.lip_sync_smooth_window,
                )
                if not getattr(self.gpt, "ready", False):
                    self.gpt = None
            except Exception as e:
                if self.verbose:
                    print(f"⚠️ [TTS] GPT-SoVITS 初始化失败: {e}")
                self.gpt = None

        self._active = "gpt" if self.gpt else "edge"
        self.role_tts_config = {}

        self._q: asyncio.Queue[SpeakItem] = asyncio.Queue()  # 文本队列
        self._audio_q: asyncio.Queue[AudioItem] = asyncio.Queue()  # 音频队列 (流水线)

        self._worker_task: Optional[asyncio.Task] = None
        self._player_task: Optional[asyncio.Task] = None  # 新增：播放线程
        self._interrupt_event = asyncio.Event()

        self._current_stream_id = 0
        self._stream_buffer: Optional[StreamSentenceBuffer] = None
        self._emo_tag_any_re = re.compile(
            r"<\s*/?\s*(?:emo(?:tion)?|happy|sad|angry|shy|flustered|confused|neutral|think|idle)\b[^>]*>",
            flags=re.IGNORECASE,
        )
        self._cmd_re = re.compile(r"\[CMD:.*?\]", flags=re.DOTALL)
        self.enabled = True

    def _ensure_gpt_instance(self):
        if self.gpt is not None or GPTSoVITSTTS is None:
            return
        try:
            self.gpt = GPTSoVITSTTS(
                enable_lip_sync=self.enable_lip_sync,
                rhubarb_path=self.rhubarb_path,
                lip_sync_smooth_window=self.lip_sync_smooth_window,
            )
            if not getattr(self.gpt, "ready", False):
                self.gpt = None
        except Exception as e:
            if self.verbose:
                print(f"⚠️ [TTS] GPT-SoVITS 延迟初始化失败: {e}")
            self.gpt = None

    def _sanitize_tts_text(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        t = self._cmd_re.sub("", t)
        t = self._emo_tag_any_re.sub("", t)
        return t.strip()

    def _has_pending_output(self) -> bool:
        # 流式回复在 stop_stream 前允许短暂“队列见底”，这时还不能判定为彻底播完。
        return (
            self._stream_buffer is not None
            or (not self._q.empty())
            or (not self._audio_q.empty())
        )

    def _ensure_worker(self):
        if not self._worker_task or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._synthesis_loop()
            )  # 改名：合成循环
        if not self._player_task or self._player_task.done():
            self._player_task = asyncio.create_task(
                self._player_loop()
            )  # 新增：播放循环

    def _resolve_tail_padding(self, item: AudioItem) -> float:
        padding = float(getattr(item, "tail_padding", self.final_pause_sec) or 0.0)
        padding = max(0.0, padding)

        # 同一轮里已经明确还有后续音频时，尽量缩短句间停顿；
        # 真正收尾时仍保留更稳妥的尾缓冲。
        if padding <= self.segment_pause_sec:
            return padding
        if not self._audio_q.empty() or not self._q.empty():
            return self.segment_pause_sec
        return padding

    def _switch_to(self, backend: str, reason: str = ""):
        if backend == self._active:
            return
        self._active = backend
        if self.verbose:
            print(
                f"🔁 [TTS] 切换后端 -> {'GPT-SoVITS' if backend == 'gpt' else 'Edge-TTS'} ({reason})"
            )

    def apply_role_tts_config(self, cfg: Optional[dict] = None):
        cfg = cfg if isinstance(cfg, dict) else {}
        self.role_tts_config = cfg
        if cfg.get("enabled"):
            self._ensure_gpt_instance()
        if self.gpt and hasattr(self.gpt, "apply_runtime_config"):
            self.gpt.apply_runtime_config(cfg)
            if cfg.get("enabled") and getattr(self.gpt, "ready", False):
                self._switch_to("gpt", "角色TTS配置生效")
                return
        if cfg.get("enabled"):
            if self.verbose:
                print(
                    "⚠️ [TTS] 角色TTS已启用，但 GPT-SoVITS 当前未就绪，回退到 Edge-TTS"
                )
        self._switch_to("edge", "角色TTS未就绪")

    def _current_prompt_lang(self) -> str:
        cfg = self.role_tts_config if isinstance(self.role_tts_config, dict) else {}
        return str(cfg.get("prompt_lang") or "ja").strip().lower()

    async def _translate_to_jp(self, text: str) -> str:
        if not text or not chat_with_ai:
            return text
        try:
            prompt = f"""
Task: Convert input text into **natural, spoken Japanese** (Anime girl style).
Rules:
1. Translate Chinese/English sentences to natural Japanese.
2. Keep the meaning and length close to the original. Do not add fillers, explanations, or extra commentary.
3. **CRITICAL**: Convert English terms/names (Python, API) to **Katakana** (パイソン, エーピーアイ).
4. Tone: cute, casual.
5. Output only the translated Japanese text. No labels, no quotes, no markdown.
Input: "{text}"
Output:
"""
            jp_text = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "user", "content": prompt}],
                task_type="translation",
                caller="tts_translate",
            )
            return self._clean_translated_text(jp_text)
        except Exception:
            return text

    def _clean_translated_text(self, text: str) -> str:
        raw = str(text or "").replace("\r", "\n").strip()
        if not raw:
            return ""
        raw = re.sub(r"```(?:\w+)?\n?|```", "", raw)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return ""
        cleaned: list[str] = []
        skip_prefixes = (
            "input:",
            "input：",
            "原文:",
            "原文：",
            "输入:",
            "输入：",
            "text:",
            "text：",
        )
        label_re = re.compile(
            r"^(?:output|translation|translated text|japanese|jp|ja|日文|翻译|翻訳|訳文|出力|结果)\s*[:：]\s*",
            flags=re.IGNORECASE,
        )
        for line in lines:
            low = line.lower()
            if low.startswith(skip_prefixes):
                continue
            line = label_re.sub("", line).strip()
            line = line.strip(" \"'“”‘’「」『』")
            if not line:
                continue
            cleaned.append(line)
        if not cleaned:
            return re.sub(r"\s+", " ", raw).strip(" \"'“”‘’「」『』")
        return "\n".join(cleaned).strip()

    # ==================== 接口逻辑 ====================

    async def synthesize_once(
        self, text: str, emotion: Optional[str] = None
    ) -> tuple[Optional[str], float]:
        if not self.enabled:
            return None, 0.0
        clean = self._sanitize_tts_text(text)
        if not clean:
            return None, 0.0

        text_to_speak = clean
        if (
            self.gpt
            and self._active == "gpt"
            and TTS_AUTO_TRANSLATE
            and len(clean) > 1
            and self._current_prompt_lang() in {"ja", "jp", "japanese"}
        ):
            jp = await self._translate_to_jp(clean)
            if jp and len(jp) > 1:
                text_to_speak = jp

        if self.gpt and self._active == "gpt":
            path, duration = await self.gpt.synthesize(text_to_speak, emotion)
            if path:
                return path, duration
            self._switch_to("edge", "GPT外发合成失败")

        return await self.edge.synthesize(clean)

    def start_stream(self):
        self._ensure_worker()
        self._current_stream_id += 1
        self._interrupt_event.set()  # 打断旧的

        # 清空所有队列
        while not self._q.empty():
            self._q.get_nowait()
            self._q.task_done()
        while not self._audio_q.empty():
            item = self._audio_q.get_nowait()
            # 尝试删除未播放的文件
            try:
                import os

                if os.path.exists(item.audio_path):
                    os.remove(item.audio_path)
            except:
                pass
            self._audio_q.task_done()

        self._stream_buffer = StreamSentenceBuffer(
            max_chars=self.chunk_chars_default if self.split_long_default else None
        )
        if self.verbose:
            print(f"🌊 [TTS] 流式会话开始 ID={self._current_stream_id}")

    async def feed_stream(self, chunk: str, emotion: Optional[str] = None):
        if self._stream_buffer is None:
            self.start_stream()
        for sentence in self._stream_buffer.feed(chunk):
            await self._add_stream_item(sentence, emotion)

    async def stop_stream(self, emotion: Optional[str] = None):
        if self._stream_buffer:
            for sentence in self._stream_buffer.close():
                await self._add_stream_item(sentence, emotion)
            self._stream_buffer = None

    async def _add_stream_item(self, text: str, emotion: str | None):
        clean = self._sanitize_tts_text(text)
        if not clean:
            return
        item = SpeakItem(
            text=clean,
            emotion=emotion,
            interrupt=False,
            split_long=self.split_long_default,
            chunk_chars=self.chunk_chars_default,
            show_bubble=True,
        )
        await self._q.put(item)

    async def say(
        self,
        text: str,
        emotion: Optional[str] = None,
        *,
        interrupt: bool = True,
        split_long=None,
        chunk_chars=None,
        show_bubble=True,
    ):
        if not self.enabled:
            return
        text = self._sanitize_tts_text(text)
        if not text:
            return
        self._ensure_worker()

        if interrupt:
            if self.verbose:
                print("🛑 [TTS] 收到打断请求")
            self._interrupt_event.set()
            # 立即停止当前声音
            await stop_sound()

            self._current_stream_id += 1
            self._stream_buffer = None

        item = SpeakItem(
            text=text,
            emotion=emotion,
            interrupt=interrupt,
            split_long=self.split_long_default
            if split_long is None
            else bool(split_long),
            chunk_chars=self.chunk_chars_default
            if chunk_chars is None
            else int(chunk_chars),
            show_bubble=show_bubble,
        )
        await self._q.put(item)

    # ==================== 🧵 线程 1: 翻译与合成 (生产者) ====================
    async def _synthesis_loop(self):
        print("🔧 [TTS Synthesis] 合成线程启动")
        while True:
            try:
                item = await self._q.get()

                if not self.enabled:
                    self._q.task_done()
                    while not self._audio_q.empty():
                        try:
                            self._audio_q.get_nowait()
                            self._audio_q.task_done()
                        except Exception:
                            break
                    continue

                # 处理打断逻辑
                if item.interrupt:
                    while not self._q.empty():
                        self._q.get_nowait()
                        self._q.task_done()
                    while not self._audio_q.empty():
                        self._audio_q.get_nowait()
                        self._audio_q.task_done()

                self._interrupt_event.clear()
                if self._interrupt_event.is_set():
                    self._q.task_done()
                    continue

                segments = [item.text]
                if item.split_long:
                    segments = _split_text(item.text, item.chunk_chars) or [item.text]

                for idx, seg in enumerate(segments):
                    if self._interrupt_event.is_set():
                        break
                    is_last_segment = idx == (len(segments) - 1)
                    tail_padding = (
                        self.final_pause_sec
                        if is_last_segment
                        else self.segment_pause_sec
                    )

                    # 1. 准备文本 (中译日)
                    text_to_speak = seg
                    if (
                        self.gpt
                        and self._active == "gpt"
                        and TTS_AUTO_TRANSLATE
                        and len(seg) > 1
                        and self._current_prompt_lang() in {"ja", "jp", "japanese"}
                    ):
                        jp = await self._translate_to_jp(seg)
                        if jp and len(jp) > 1:
                            if self.verbose:
                                print(f"🈯 [TTS] 翻译: {seg[:10]} -> {jp[:10]}")
                            text_to_speak = jp

                    # 2. 合成音频 (不播放)
                    audio_path = None
                    duration = 0.0
                    backend = None

                    # 尝试 GPT
                    if self.gpt and self._active == "gpt":
                        path, dur = await self.gpt.synthesize(
                            text_to_speak, item.emotion
                        )
                        if path:
                            audio_path = path
                            duration = dur
                            backend = self.gpt
                        else:
                            self._switch_to("edge", "GPT生成失败")
                            text_to_speak = seg  # 回退中文

                    # 尝试 Edge
                    if not audio_path and (not self._interrupt_event.is_set()):
                        if self._active != "edge":
                            self._switch_to("edge")
                        path, dur = await self.edge.synthesize(seg)  # Edge读中文
                        if path:
                            audio_path = path
                            duration = dur
                            backend = self.edge

                    # 3. 推入音频队列 (如果生成成功)
                    if audio_path:
                        audio_item = AudioItem(
                            audio_path=audio_path,
                            duration=duration,
                            text_for_bubble=seg,  # 气泡显示原文
                            emotion=item.emotion,
                            backend=backend,
                            tail_padding=tail_padding,
                        )
                        await self._audio_q.put(audio_item)
                        if self.verbose:
                            print(f"📦 [TTS] 音频已入队 ({duration:.1f}s)")

                    # 如果全部生成失败，推入一个"静默"项用于显示气泡
                    elif not self._interrupt_event.is_set():
                        # 兜底：只显示气泡
                        est_dur = max(2.0, len(seg) * 0.3)
                        await self._audio_q.put(
                            AudioItem(
                                None,
                                est_dur,
                                seg,
                                item.emotion,
                                None,
                                tail_padding=tail_padding,
                            )
                        )

                self._q.task_done()
            except Exception as e:
                print(f"💥 [Synthesis] 错误: {e}")
                await asyncio.sleep(1)

    # ==================== 🧵 线程 2: 播放与气泡 (消费者) ====================
    async def _player_loop(self):
        print("🔊 [TTS Player] 播放线程启动")
        while True:
            try:
                # 获取下一个待播放的音频
                item: AudioItem = await self._audio_q.get()

                # 再次检查打断
                if self._interrupt_event.is_set():
                    if item.audio_path:
                        try:
                            import os

                            if os.path.exists(item.audio_path):
                                os.remove(item.audio_path)
                        except:
                            pass
                    self._audio_q.task_done()
                    continue

                if self.sm:
                    await self.sm.set_state(AgentState.SPEAKING, backend=self._active)

                if item.audio_path and item.backend:
                    # play_audio_file 里可能先做口型分析；气泡必须等播放指令发出后再计时。
                    await item.backend.play_audio_file(
                        item.audio_path, self._interrupt_event, item.emotion
                    )

                    bubble_ms = int(item.duration * 1000) + 500
                    read_ms = estimate_bubble_display_ms(item.text_for_bubble)
                    display_ms = max(bubble_ms, int(read_ms))
                    wait_time = max(display_ms / 1000.0, max(0, item.duration)) + self._resolve_tail_padding(item)
                    if self.bubble_sender:
                        # 将秒转为毫秒，气泡显示时间稍微比音频长一点点 (500ms) 增加连贯性
                        asyncio.create_task(
                            self.bubble_sender(item.text_for_bubble, item.emotion, display_ms)
                        )

                    # 按“中间段短、收尾段稳”的策略等待：
                    # - 同一轮内部的分段句间停顿更短；
                    # - 最后一段仍保留更稳妥的尾缓冲，避免截尾或抢状态。
                    slept = 0
                    while slept < wait_time:
                        # 实时检查是否被打断
                        if self._interrupt_event.is_set():
                            # 立即通知前端停止当前频道声音
                            try:
                                from modules.live2d import stop_sound

                                await stop_sound()
                            except:
                                pass
                            break
                        await asyncio.sleep(0.1)
                        slept += 0.1
                else:
                    read_ms = estimate_bubble_display_ms(item.text_for_bubble)
                    ms = max(int(item.duration * 1000) + 500, int(read_ms))
                    if self.bubble_sender:
                        asyncio.create_task(
                            self.bubble_sender(item.text_for_bubble, item.emotion, ms)
                        )
                    # 静默兜底模式
                    await asyncio.sleep(ms / 1000.0)

                # 任务完成
                self._audio_q.task_done()

                # 检查是否全部播完且没有新任务
                if (not self._has_pending_output()) and not self._interrupt_event.is_set():
                    # 给下一段极短的合成/入队留一点缓冲，避免段与段之间误判为彻底结束。
                    await asyncio.sleep(0.25)
                if (not self._has_pending_output()) and not self._interrupt_event.is_set():
                    if self.verbose:
                        print("✅ [TTS Player] 队列清空，返回空闲状态")
                    if self.sm:
                        await self.sm.set_state(AgentState.IDLE, reason="all_done")
                    if self.go_idle_fn:
                        await self.go_idle_fn()

            except Exception as e:
                print(f"💥 [Player] 错误: {e}")
                await asyncio.sleep(1)
