# modules/state_machine.py
from __future__ import annotations

import asyncio
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Any, List, Optional

from collections import deque
class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass
class StateSnapshot:
    state: AgentState
    prev: AgentState
    meta: dict


Listener = Callable[[AgentState, AgentState, dict], Any]


class AgentStateMachine:
    """
    统一状态机（轻量、线程安全由 asyncio.Lock 保证）：
      - set_state(): 串行化状态变更，避免并发乱跳
      - add_listener(): 订阅状态变化（可驱动 Live2D 动作/GUI 显示）
    """
    def __init__(self, initial: AgentState = AgentState.IDLE, history_size: int = 20):
        self._state: AgentState = initial
        self._lock: Optional[asyncio.Lock] = None
        self._listeners: List[Listener] = []
        self._history: deque = deque(maxlen=history_size)


    @property
    def state(self) -> AgentState:
        return self._state

    def add_listener(self, fn: Listener):
        self._listeners.append(fn)

    async def set_state(self, new_state: AgentState, **meta):
        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            prev = self._state
            if prev == new_state:
                return

            self._history.append(StateSnapshot(new_state, prev, meta))
            self._state = new_state
            for fn in list(self._listeners):
                try:
                    ret = fn(new_state, prev, meta)
                    if asyncio.iscoroutine(ret):
                        await ret
                except Exception as e:
                    # 监听器异常不能影响主流程
                    print(f"⚠️ [State] listener error: {e}")


    def get_history(self) -> List[StateSnapshot]:
        return list(self._history)
