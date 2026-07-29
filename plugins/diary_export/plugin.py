from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from modules.memory_sqlite import get_memory_store


class Plugin:
    def __init__(self):
        self.name = "Diary Manager"
        self.description = "日记查询与导出工具"
        self.output_dir = "./output"
        os.makedirs(self.output_dir, exist_ok=True)

    async def run(self, args: str, context: dict) -> str:
        del context
        text = str(args or "").strip().lower()
        if any(keyword in text for keyword in ("export", "导出", "save", "保存", "备份")):
            return self._export_all_diaries()
        return self._query_diary(text)

    @staticmethod
    def _is_diary(row: Dict[str, Any]) -> bool:
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        return "daily_log" in {str(tag).strip() for tag in tags}

    def _list_diaries(self) -> List[Dict[str, Any]]:
        store = get_memory_store()
        rows = store.list_episodes(status="active", limit=500, offset=0)
        return [row for row in rows if self._is_diary(row)]

    def _export_all_diaries(self) -> str:
        try:
            rows = self._list_diaries()
            if not rows:
                return "数据库里还没有任何日记记录，无法导出。"
            now = datetime.datetime.now()
            lines = [f"# 角色日记\n\n> 导出时间：{now:%Y-%m-%d %H:%M}\n"]
            for row in rows:
                lines.append(f"\n## {row.get('title') or '未命名日记'}\n")
                lines.append(str(row.get("summary") or "").strip())
                lines.append("\n\n---\n")
            filename = f"Diary_Export_{now:%Y%m%d_%H%M}.md"
            path = Path(self.output_dir) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
            return f"导出成功，共处理 {len(rows)} 篇日记。文件已保存至：`{path.resolve()}`"
        except Exception as exc:
            return f"导出过程出错: {exc}"

    def _query_diary(self, text: str) -> str:
        try:
            target = datetime.datetime.now()
            description = "今天"
            date_match = re.search(r"(\d{4})[-年/. ](\d{1,2})[-月/. ](\d{1,2})", text)
            if date_match:
                year, month, day = map(int, date_match.groups())
                target = datetime.datetime(year, month, day)
                description = target.strftime("%Y-%m-%d")
            elif "大前天" in text:
                target -= datetime.timedelta(days=3)
                description = "大前天"
            elif "前天" in text:
                target -= datetime.timedelta(days=2)
                description = "前天"
            elif "昨" in text or "yesterday" in text:
                target -= datetime.timedelta(days=1)
                description = "昨天"
            date_text = target.strftime("%Y-%m-%d")
            rows = [
                row
                for row in self._list_diaries()
                if date_text in str(row.get("title") or "")
                or date_text in str(row.get("created_at") or "")
                or f"date:{date_text}" in {str(tag) for tag in row.get("tags") or []}
            ]
            if not rows:
                return f"没有找到 {description}（{date_text}）的日记。"
            lines = [f"找到了 {description} 的 {len(rows)} 篇日记："]
            for row in rows:
                lines.append(f"\n【{row.get('title') or '未命名日记'}】")
                lines.append(str(row.get("summary") or "").strip())
            return "\n".join(lines)
        except Exception as exc:
            return f"查询出错: {exc}"
