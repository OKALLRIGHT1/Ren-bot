import os
import glob
import asyncio


class Plugin:
    async def run(self, args: str, context: dict) -> str:
        # 从上下文获取 brain 实例
        brain = context.get("brain")
        if not brain:
            return "❌ 内部错误：无法访问记忆系统 (Brain Not Found)"

        if "|||" not in args:
            return "❌ 格式错误，请使用: 指令 ||| 内容"

        cmd, content = args.split("|||", 1)
        cmd = cmd.strip().lower()
        content = content.strip()

        # --- 功能 1: 学习 (Ingest) ---
        if cmd == "learn":
            if not os.path.exists(content):
                return f"❌ 路径不存在: {content}"

            # 扫描 .md, .txt, .py 文件
            files = []
            for ext in ["*.md", "*.txt", "*.py", "*.json"]:
                # 递归查找
                files.extend(glob.glob(os.path.join(content, "**", ext), recursive=True))

            if not files:
                return "⚠️ 该目录下没有找到支持的文档格式 (.md/.txt/.py)"

            total_chunks = 0

            # 这是一个耗时操作，建议放到线程池
            def _do_import():
                count = 0
                for fpath in files:
                    # 调用 AdvancedMemorySystem 现有的导入方法
                    c = brain.import_knowledge_from_file(fpath)
                    count += c
                return count

            try:
                total_chunks = await asyncio.to_thread(_do_import)
                return f"✅ 学习完成！共扫描 {len(files)} 个文件，存入 {total_chunks} 条知识片段。"
            except Exception as e:
                return f"❌ 学习过程中出错: {e}"

        # --- 功能 2: 搜索 (Search) ---
        elif cmd == "search":
            # 调用 brain 的检索方法
            # 注意：brain._retrieve_knowledge 是内部方法，但Python里可以直接调
            # 或者你可以给 AdvancedMemorySystem 加一个 public 方法
            try:
                # 假设 content 是查询词
                results = await asyncio.to_thread(brain._retrieve_knowledge, content, k=3)
                if not results:
                    return "📭 知识库中没有找到相关内容。"

                return f"📚 检索结果:\n" + "\n---\n".join(results)
            except Exception as e:
                return f"❌ 检索失败: {e}"

        return "❌ 未知指令，请使用 learn 或 search"