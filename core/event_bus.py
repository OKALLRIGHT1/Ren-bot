"""
事件总线
用于组件间解耦通信
"""
import asyncio
from typing import Callable, Dict, List, Any, Optional, Tuple


class EventBus:
    """增强的事件总线"""

    # 修改 EventBus 类的 __init__ 方法
    def __init__(self):
        self._subs: Dict[str, List[Tuple[int, Callable]]] = {}  # ✅ 改进: 添加优先级
        self._wildcards: List[Tuple[int, Callable]] = []
        self._lock = asyncio.Lock()

    # 修改 on 方法
    def on(self, name: str, fn: Callable, priority: int = 0) -> None:
        """
        订阅事件（支持优先级）

        Args:
            name: 事件名称，使用"*"订阅所有事件
            fn: 事件处理函数
            priority: 优先级（数字越大优先级越高，默认0）
        """
        name = (name or "").strip()
        if not name:
            return

        if name == "*":
            self._wildcards.append((priority, fn))
            # ✅ 按优先级排序（降序）
            self._wildcards.sort(key=lambda x: x[0], reverse=True)
            return

        self._subs.setdefault(name, []).append((priority, fn))
        # ✅ 按优先级排序（降序）
        self._subs[name].sort(key=lambda x: x[0], reverse=True)

    # 修改 off 方法
    def off(self, name: str, fn: Optional[Callable] = None) -> None:
        """
        取消订阅事件

        Args:
            name: 事件名称
            fn: 要取消的处理函数，如果为None则移除所有该事件的订阅
        """
        name = (name or "").strip()
        if not name:
            return

        if name == "*":
            if fn:
                self._wildcards = [(p, f) for p, f in self._wildcards if f != fn]
            else:
                self._wildcards = []
            return

        if fn:
            self._subs[name] = [(p, f) for p, f in self._subs.get(name, []) if f != fn]
        else:
            self._subs.pop(name, None)

    # 修改 emit 方法

    async def emit(self, name: str, **payload: Any) -> None:
        """
        发出事件（异常隔离 + 优先级执行）
        ✅ 改进：锁内只取订阅者快照；锁外执行，避免阻塞整个 EventBus。
        """
        name = (name or "").strip()
        if not name:
            return

        # 锁内只拷贝快照
        async with self._lock:
            wildcards = list(self._wildcards)
            subs = list(self._subs.get(name, []))

        errors = []

        # 通配订阅者
        for priority, fn in wildcards:
            try:
                ret = fn(name, payload)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception as e:
                errors.append((getattr(fn, "__name__", str(fn)), e))

        # 指定事件订阅者
        for priority, fn in subs:
            try:
                ret = fn(payload)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception as e:
                errors.append((getattr(fn, "__name__", str(fn)), e))

        if errors:
            for fn_name, e in errors:
                print(f"⚠️ [EventBus:{name}] {fn_name} 错误: {e}")

    def emit_sync(self, name: str, **payload: Any) -> None:
        """
        同步触发：尽量不在没有 running loop 的线程里 asyncio.run()
        - 若当前线程有 running loop：create_task 投递
        - 否则：退化为“尽力而为”的非阻塞投递（不强行跑 async）
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit(name, **payload))
        except RuntimeError:
            # 当前线程没有 running loop：不要 asyncio.run()，避免跨线程/跨 loop 副作用
            # 这里选择静默忽略或打印提示（你也可改成写日志）
            print(f"⚠️ [EventBus] emit_sync skipped (no running loop): {name}")

    def clear(self) -> None:
        """清除所有订阅"""
        self._subs.clear()
        self._wildcards.clear()


# 事件名称常量
class Events:
    """事件名称常量"""
    STATE_CHANGED = "state.changed"
    UI_STATUS = "ui.status"
    UI_APPEND = "ui.append"
    UI_BUBBLE = "ui.bubble"
    LIVE2D_EMOTION = "live2d.emotion"
    LIVE2D_MOTION = "live2d.motion"
    LIVE2D_GO_IDLE = "live2d.go_idle"
    ASSISTANT_UTTER = "assistant.utter"
    ASSISTANT_STREAM_START = "assistant.stream.start"
    ASSISTANT_STREAM_FEED = "assistant.stream.feed"
    ASSISTANT_STREAM_END = "assistant.stream.end"
    CHAT_LOG = "chat.log"
    MEMORY_ADD_OK = "memory.add.ok"
    MEMORY_ADD_FAIL = "memory.add.fail"
