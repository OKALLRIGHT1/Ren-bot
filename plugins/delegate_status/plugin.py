import re
from typing import Dict, List

from modules.delegate_session import get_recent as get_recent_delegate_events
from modules.delegate_task_state import get_recent_tasks, get_task


class Plugin:
    name = "副脑任务状态"
    type = "direct"
    description = "查看最近的副脑委托任务，或按任务ID查看详情。"
    aliases = [
        "副脑任务",
        "副脑状态",
        "委托任务",
        "委托状态",
        "最近副脑任务",
        "最近委托任务",
    ]

    def should_handle_direct(self, text: str, context: Dict, key: str) -> bool:
        raw = str(text or "")
        return any(alias in raw for alias in self.aliases)

    async def run(self, args: str, ctx: Dict) -> str:
        text = str(args or "").strip()
        task_id = self._extract_task_id(text)
        if task_id:
            task = get_task(task_id)
            if not task:
                return f"未找到副脑任务 `{task_id}`。"
            return self._format_task(task, detailed=True)

        items = get_recent_tasks(limit=5)
        if not items:
            return "当前还没有副脑任务记录。"
        lines = ["最近副脑任务："]
        for item in items:
            lines.append(self._format_task(item, detailed=False))
        lines.append("如需详情，可说：副脑任务 <task_id>")
        return "\n".join(lines)

    def _extract_task_id(self, text: str) -> str:
        match = re.search(r"\b([0-9a-fA-F]{10})\b", text or "")
        return str(match.group(1)).strip() if match else ""

    def _format_task(self, task: Dict, *, detailed: bool) -> str:
        task_id = str(task.get("task_id") or "")
        state = str(task.get("state") or "unknown")
        summary = str(task.get("summary") or "").strip()
        source = str(task.get("source") or "").strip() or "unknown"
        updated_at = str(task.get("updated_at") or "")
        triggers = [
            str(item).strip()
            for item in (task.get("triggers") or [])
            if str(item).strip()
        ]
        trigger_text = ", ".join(triggers[:4]) if triggers else "-"
        if not detailed:
            return f"- {task_id} | {state} | {trigger_text} | {summary[:60] or '-'}"

        lines: List[str] = [
            f"副脑任务 {task_id}",
            f"- 状态: {state}",
            f"- 来源: {source}",
            f"- 触发能力: {trigger_text}",
            f"- 更新时间: {updated_at or '-'}",
            f"- 摘要: {summary or '-'}",
        ]
        meta = task.get("meta") or {}
        if isinstance(meta, dict) and meta:
            delegate_used = meta.get("delegate_used") or []
            route_reason = str(meta.get("route_reason") or "").strip()
            if route_reason:
                lines.append(f"- 路由原因: {route_reason}")
            if delegate_used:
                used_text = ", ".join(
                    str(item).strip() for item in delegate_used if str(item).strip()
                )
                if used_text:
                    lines.append(f"- 实际调用: {used_text}")
        history = task.get("history") or []
        if isinstance(history, list) and history:
            lines.append("- 最近状态流转:")
            for item in history[-5:]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  {str(item.get('time') or '')} | {str(item.get('state') or '')} | {str(item.get('summary') or '')[:60]}"
                )
        if task_id:
            events = get_recent_delegate_events(limit=5, task_id=task_id)
            if events:
                lines.append("- 最近会话事件:")
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    lines.append(
                        f"  {str(event.get('time') or '')} | {str(event.get('type') or '')} | {str(event.get('text') or '')[:60]}"
                    )
        return "\n".join(lines)
