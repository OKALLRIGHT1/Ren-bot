import asyncio
import re
from typing import Any, Dict, Optional

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors, safe_get_context
from services.capability_manager import ToolCapability, ToolCapabilityMatch

logger = get_logger()


class Plugin:
    def __init__(self):
        # 持有在飞的倒计时任务：create_task 的返回值若无强引用，
        # 任务可能在 await 期间被 GC 回收，倒计时就静默失效。
        self._pending: set = set()

    async def stop(self):
        """生命周期钩子：宿主关闭/热重载时取消未完成的倒计时。"""
        pending = list(self._pending)
        self._pending.clear()
        for task in pending:
            if not task.done():
                task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    def get_capabilities(self):
        return [
            ToolCapability(
                id="timer.countdown",
                plugin="timer",
                trigger_mode="natural",
                match=self._match_countdown,
                description="倒计时或番茄钟，按分钟计时",
                examples=["倒计时 25 分钟", "开个番茄钟", "10分钟后叫我"],
            )
        ]

    def _match_countdown(self, text: str, ctx: Dict[str, Any]) -> Optional[ToolCapabilityMatch]:
        del ctx
        raw = str(text or "").strip()
        if not raw:
            return None
        if any(word in raw for word in ("关闭", "关掉", "取消", "删除", "停掉")) and (
            "倒计时" not in raw and "番茄" not in raw
        ):
            return None
        if any(word in raw for word in ("倒计时", "番茄钟", "番茄", "专注")):
            return ToolCapabilityMatch(
                capability_id="timer.countdown",
                plugin="timer",
                score=0.9,
                raw_text=raw,
                reason="countdown_phrase",
            )
        if re.search(r"\d+\s*分钟", raw):
            return ToolCapabilityMatch(
                capability_id="timer.countdown",
                plugin="timer",
                score=0.86,
                raw_text=raw,
                reason="minutes_countdown",
            )
        return None

    def get_tool_schema(self):
        return {
            "name": "timer",
            "description": "设定倒计时或番茄钟，参数为分钟数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "倒计时分钟数，默认 25",
                    }
                },
                "required": ["minutes"],
            },
        }

    def format_invocation(self, arguments):
        args = dict(arguments or {})
        minutes = args.get("minutes")
        if minutes is None:
            minutes = args.get("query") or args.get("text") or 25
        return str(minutes).strip()

    @handle_plugin_errors("番茄钟/倒计时")
    async def run(self, args, ctx):
        try:
            # 尝试提取数字，支持 "25分钟" 这种写法
            num = re.search(r'\d+', args)
            minutes = int(num.group()) if num else 25
        except (ValueError, AttributeError) as e:
            logger.warning(f"倒计时参数解析失败: {args}, 错误: {e}")
            minutes = 25  # 默认值

        send = safe_get_context(ctx, 'send_bubble')

        # 启动后台任务，不阻塞当前回复
        task = asyncio.create_task(self._timer_logic(minutes, send))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

        logger.info(f"倒计时启动: {minutes}分钟")
        return f"好的，已设定一个 {minutes} 分钟的倒计时，开始专注吧！"

    async def _timer_logic(self, minutes, send_func):
        try:
            logger.debug(f"倒计时开始，等待 {minutes * 60} 秒")
            await asyncio.sleep(minutes * 60)
            logger.info(f"倒计时结束: {minutes}分钟")

            if send_func:
                # 时间到时，主动发送气泡和语音
                await send_func(f"⏰ 时间到啦！专注了 {minutes} 分钟，该休息一下了~")
        except Exception as e:
            logger.error(f"倒计时逻辑异常: {e}")
