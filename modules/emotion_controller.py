# modules/emotion_controller.py
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
from modules.state_machine import AgentState
from modules import live2d

try:
    from config import EMO_TO_LIVE2D
except Exception:
    EMO_TO_LIVE2D = {}

# 情绪保持配置
EMO_DECAY_TIMEOUT = 300.0  # 衰减阈值(秒)：超过5分钟无互动，情绪回归平静
NEUTRAL_LABELS = {"neutral", "idle", "default"}


class EmotionController:
    def __init__(self, mapping: Optional[Dict[str, Any]] = None) -> None:
        self.mapping: Dict[str, Any] = mapping or EMO_TO_LIVE2D or {}
        self.agent_state: AgentState = AgentState.IDLE

        # 状态核心
        self.current_emotion: str = "neutral"
        self.current_intensity: float = 0.3
        self.last_emotion_change: float = time.time()  # 上次情绪改变时间
        self.last_activity: float = time.time()  # 上次互动时间

        self._lock = asyncio.Lock()

        # 🔴 [删除] 下面这行代码，不要在 init 里启动任务！
        # asyncio.create_task(self._decay_loop())

    # 🟢 [新增] 这个方法，用于手动启动
    def start(self, loop):
        """启动后台衰减循环"""
        # 使用传入的 loop 创建任务，这比 asyncio.create_task 更稳定
        loop.create_task(self._decay_loop())

    def mark_activity(self, why: str = "") -> None:
        """标记有活动发生（防止衰减）"""
        self.last_activity = time.time()

    def set_agent_state(self, st: AgentState) -> None:
        self.agent_state = st
        if st != AgentState.IDLE:
            self.mark_activity("state_change")

    async def request_emotion(self, label: str, intensity: Optional[float] = None,
                              prefer_motion: Optional[bool] = None, reason: str = "") -> None:
        """请求切换情绪"""
        new_emo = (label or "neutral").lower()
        new_intensity = float(intensity) if intensity is not None else 0.5

        # 执行模型给出的情绪，不再用本地惯性规则拦截 neutral/idle。
        await self._apply_emotion(new_emo, new_intensity, prefer_motion)

    async def _decay_loop(self):
        """后台衰减循环"""
        while True:
            await asyncio.sleep(60)  # 每分钟检查一次
            await self._check_decay()

    async def _check_decay(self):
        """检查是否需要衰减"""
        now = time.time()
        # 如果当前是 Neutral，不需要衰减
        if self.current_emotion in NEUTRAL_LABELS:
            return

        # 如果超过 5 分钟没互动
        if now - self.last_activity > EMO_DECAY_TIMEOUT:
            print(f"📉 [Emotion] 情绪自然衰减: {self.current_emotion} -> neutral")
            await self._apply_emotion("neutral", 0.3, prefer_motion=False)

    async def _apply_emotion(self, emo: str, intensity: float, prefer_motion: Optional[bool]):
        async with self._lock:
            # 记录状态
            if emo != self.current_emotion:
                self.last_emotion_change = time.time()

            self.current_emotion = emo
            self.current_intensity = intensity

            # 查找配置
            cfg = live2d.resolve_emotion_config(emo, self.mapping)
            if not cfg:
                # 尝试 fallback
                if emo == "music":  # 如果没有 music 动作，用 happy 代替
                    cfg = live2d.resolve_emotion_config("happy", self.mapping)
                else:
                    cfg = live2d.resolve_emotion_config("neutral", self.mapping)

            if not cfg: return

            # 1. 设置表情 (Expression)
            exp_id = cfg.get("exp")
            logger = live2d._get_logger()
            logger.info(
                f"[EmotionController] emotion={emo} intensity={intensity:.2f} "
                f"prefer_motion={prefer_motion} exp={exp_id} mtn={cfg.get('mtn')} "
                f"type={cfg.get('type', 0)}"
            )
            if exp_id is not None:
                try:
                    await live2d.set_expression(int(exp_id))
                except Exception:
                    pass

            # 2. 触发动作 (Motion)
            mtn = cfg.get("mtn")
            if mtn:
                should_play = False
                # Keep think motion only in THINKING state.
                think_motion_blocked = (emo == "think" and self.agent_state != AgentState.THINKING)
                if not think_motion_blocked:
                    # 只要模型已经给出情绪，就稳定执行对应动作；需要静默时由调用方
                    # 显式传 prefer_motion=False。避免低强度情绪被随机概率吞掉。
                    should_play = prefer_motion is not False

                if should_play:
                    try:
                        await live2d.play_motion(mtn, motion_type=int(cfg.get("type", 0)))
                    except Exception:
                        pass

    async def maybe_enter_idle(self):
        """进入空闲动作，但不清理当前表情。

        说话结束后只让动作回到 idle/default；表情由模型下一次情绪判断或
        后台自然衰减接管，避免刚说完就被硬切成 neutral。
        """
        cfg = live2d.resolve_emotion_config("idle", self.mapping)
        if not cfg:
            cfg = live2d.resolve_emotion_config("neutral", self.mapping)

        if not cfg:
            return

        mtn = cfg.get("mtn")
        if not mtn:
            return

        try:
            await live2d.play_motion(str(mtn), motion_type=int(cfg.get("type", 0)))
        except Exception:
            pass
