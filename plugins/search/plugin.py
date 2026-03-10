from duckduckgo_search import DDGS
from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

logger = get_logger()


class Plugin:
    @handle_plugin_errors("联网搜索")
    async def run(self, args, ctx):
        query = args.strip()
        if not query:
            logger.warning("搜索词为空")
            return "❌ 搜索词不能为空。"

        logger.info(f"正在搜索: {query}")

        try:
            # 使用上下文管理器，并限制结果数量为 3
            # region: "cn-zh" 优化中文搜索结果
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region="cn-zh", max_results=3))

            if not results:
                logger.warning(f"未找到搜索结果: {query}")
                return f"未找到关于 '{query}' 的相关结果。"

            # 整理结果格式，供 LLM 阅读
            summary = [f"关于 '{query}' 的搜索结果："]
            for i, res in enumerate(results):
                title = res.get('title', '无标题')
                body = res.get('body', '无内容')
                href = res.get('href', '')
                summary.append(f"[{i + 1}] 标题：{title}\n    摘要：{body}\n    来源：{href}")

            logger.info(f"搜索成功，找到 {len(results)} 条结果")
            return "\n".join(summary)

        except Exception as e:
            logger.error(f"搜索错误: {e}")
            return f"搜索时发生网络错误或接口限制: {e}"
