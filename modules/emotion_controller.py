# modules/emotion_controller.py
from __future__ import annotations
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
from modules.state_machine import AgentState
from modules import live2d

try:
    from config import EMO_TO_LIVE2D
except Exception:
    EMO_TO_LIVE2D = {}

# 情绪惯性配置
EMO_INERTIA_WINDOW = 15.0  # 惯性窗口(秒)：在此时间内，强情绪不容易被 Neutral 覆盖
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
        self.last_idle_motion_at: float = 0.0

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

        # 1. 情绪惯性逻辑 (Inertia)
        # 如果新请求是 Neutral，但当前情绪很强烈且是最近发生的，则忽略 Neutral
        # (让笑容多停留一会儿)
        now = time.time()
        is_requesting_neutral = new_emo in NEUTRAL_LABELS
        is_current_strong = self.current_emotion not in NEUTRAL_LABELS and self.current_intensity > 0.4
        is_recent = (now - self.last_emotion_change) < EMO_INERTIA_WINDOW

        if is_requesting_neutral and is_current_strong and is_recent:
            print(f"🧊 [Emotion] 触发情绪惯性，保持 {self.current_emotion} (忽略 {new_emo})")
            return

        # 2. 执行切换
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
            if exp_id is not None:
                try:
                    await live2d.set_expression(int(exp_id))
                except Exception:
                    pass

            # 2. 触发动作 (Motion)
            # 策略：如果是明确请求 motion (prefer_motion=True)，或者随机概率
            mtn = cfg.get("mtn")
            if mtn:
                should_play = False
                # Keep think motion only in THINKING state.
                think_motion_blocked = (emo == "think" and self.agent_state != AgentState.THINKING)
                if not think_motion_blocked:
                    if prefer_motion is True:
                        should_play = True
                    elif prefer_motion is None:
                        # ??????????Neutral ???
                        prob = 0.8 if intensity > 0.6 else 0.2
                        should_play = (random.random() < prob)

                if should_play:
                    try:
                        await live2d.play_motion(mtn, motion_type=int(cfg.get("type", 0)))
                    except Exception:
                        pass

    async def maybe_enter_idle(self):
        """进入空闲状态"""
        cfg = live2d.resolve_emotion_config("idle", self.mapping)
        if not cfg:
            cfg = live2d.resolve_emotion_config("neutral", self.mapping)
        if not cfg:
            return

        mtn = cfg.get("mtn")
        if not mtn:
            return

        now = time.time()
        if now - self.last_idle_motion_at < 0.8:
            return

        try:
            await live2d.play_motion(str(mtn), motion_type=int(cfg.get("type", 0)))
            self.last_idle_motion_at = now
        except Exception:
            pass
