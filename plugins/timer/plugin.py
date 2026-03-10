import asyncio
import re
from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors, safe_get_context

logger = get_logger()


class Plugin:
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
        asyncio.create_task(self._timer_logic(minutes, send))

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
