import os
import re
import sqlite3
import datetime
from typing import List, Tuple

# 尝试导入通用工具
try:
    from modules.memory_sqlite import get_memory_store
except ImportError:
    # 兜底：假设本地开发环境
    def get_memory_store():
        class MockStore:
            db_path = "./memory_db/memory.db"

        return MockStore()


class Plugin:
    def __init__(self):
        self.name = "Diary Manager"
        self.description = "日记查询与导出工具"
        self.output_dir = "./output"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    async def run(self, args: str, context: dict) -> str:
        """
        入口函数：根据 args 决定是查询还是导出
        """
        text = str(args).strip().lower()

        # === 分支 1: 导出模式 ===
        if any(k in text for k in ["export", "导出", "save", "保存", "备份"]):
            return await self._export_all_diaries()

        # === 分支 2: 查询模式 (默认为查询) ===
        return await self._query_diary(text)

    async def _export_all_diaries(self) -> str:
        """导出所有日记到 Markdown"""
        try:
            conn, cursor = self._get_cursor()
            if not cursor:
                return "❌ 无法连接到记忆数据库。"

            # 查询所有日记类型的片段
            cursor.execute('''
                SELECT created_at, title, summary 
                FROM episodes 
                WHERE tags LIKE '%daily_log%' 
                ORDER BY created_at DESC
            ''')
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return "📂 数据库里还没有任何日记记录，无法导出。"

            # 生成 Markdown
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            md_lines = [f"# 五十铃怜的观察日记\n> 导出时间：{now_str}\n"]

            for created_at, title, summary in rows:
                # 简单清洗时间格式
                date_str = str(created_at).split('T')[0]
                md_lines.append(f"## {date_str} {title}")
                md_lines.append(f"{summary}\n")
                md_lines.append("---\n")

            # 写入文件
            filename = f"Diary_Export_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.md"
            file_path = os.path.join(self.output_dir, filename)
            # 获取绝对路径方便用户查找
            abs_path = os.path.abspath(file_path)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(md_lines))

            return f"✅ **导出成功！**\n共处理 {len(rows)} 篇日记。\n文件已保存至：`{abs_path}`"

        except Exception as e:
            return f"❌ 导出过程出错: {e}"

    async def _query_diary(self, text: str) -> str:
        """查询特定日期的日记"""
        try:
            # 1. 解析目标日期
            target_date = datetime.datetime.now()
            query_desc = "今天"

            # 正则匹配具体日期 (2024-02-14)
            date_match = re.search(r"(\d{4})[-年/. ](\d{1,2})[-月/. ](\d{1,2})", text)

            if date_match:
                y, m, d = map(int, date_match.groups())
                target_date = datetime.datetime(y, m, d)
                query_desc = target_date.strftime("%Y-%m-%d")
            else:
                # 相对日期关键词
                if "昨" in text or "yesterday" in text:
                    target_date -= datetime.timedelta(days=1)
                    query_desc = "昨天"
                elif "前天" in text:
                    target_date -= datetime.timedelta(days=2)
                    query_desc = "前天"
                elif "大前天" in text:
                    target_date -= datetime.timedelta(days=3)
                    query_desc = "大前天"

            search_date_str = target_date.strftime("%Y-%m-%d")

            # 2. 查库
            conn, cursor = self._get_cursor()
            if not cursor:
                return "❌ 数据库连接失败。"

            # 模糊匹配 created_at (通常是 ISO 格式 2024-02-14T...) 或 title
            # 只要包含这个日期字符串就算命中
            query_pattern = f"%{search_date_str}%"

            cursor.execute('''
                SELECT title, summary, tags
                FROM episodes 
                WHERE (created_at LIKE ? OR title LIKE ?)
                AND tags LIKE '%daily_log%'
                ORDER BY created_at DESC
                LIMIT 5
            ''', (query_pattern, query_pattern))

            rows = cursor.fetchall()
            conn.close()

            # 3. 格式化输出
            if not rows:
                return f"📅 翻阅了记录，没有找到 **{query_desc} ({search_date_str})** 的日记哦。\n(可能那天我休息了，或者没有生成总结)"

            out_lines = [f"📅 找到了 {query_desc} 的 {len(rows)} 条记录："]
            for title, summary, tags in rows:
                # 美化 Tag 显示
                tag_icon = "🏷️"
                if "daily_log" in str(tags):
                    tag_icon = "📝"

                out_lines.append(f"\n**{tag_icon} {title}**")
                out_lines.append(f"{summary}")

            return "\n".join(out_lines)

        except Exception as e:
            return f"❌ 查询出错: {e}"

    def _get_cursor(self):
        """辅助函数：获取数据库游标"""
        try:
            store = get_memory_store()
            db_path = getattr(store, "db_path", "./memory_db/memory.db")
            if not os.path.exists(db_path):
                return None, None

            conn = sqlite3.connect(db_path)
            return conn, conn.cursor()
        except:
            return None, None
