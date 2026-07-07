from __future__ import annotations

import re
from typing import Any, Dict, Iterable

from services.capability_manager import CapabilityManager


class Plugin:
    name = "Agent 运行时"
    type = "direct"
    description = "查看轻量 AgentRuntime 的工具目录、MCP 状态，并生成受控能力安装计划。"
    aliases = [
        "agent 自检",
        "agent 工具列表",
        "agent 能力列表",
        "agent 安装计划",
        "agent 位置",
        "代码在哪",
        "项目目录在哪",
        "agent runtime",
        "agent tools",
        "agent health",
    ]
    tool_examples = ["/agent 自检", "/agent 工具列表", "/agent 安装计划 weather"]

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        return raw.startswith(
            (
                "agent 自检",
                "agent 工具列表",
                "agent 能力列表",
                "agent 安装计划",
                "agent 位置",
                "代码在哪",
                "项目目录在哪",
                "agent runtime",
                "agent tools",
                "agent health",
            )
        )

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        text = str(args or "").strip()
        manager = self._manager(ctx)
        lowered = text.lower()

        if "位置" in text or "代码在哪" in text or "项目目录在哪" in text:
            return self._format_location(ctx)

        if "安装计划" in text or lowered.startswith("agent install"):
            capability = self._extract_capability(text)
            plan = manager.propose_install(capability)
            return self._format_install_plan(plan)

        if "工具列表" in text or "能力列表" in text or "tools" in lowered:
            report = manager.health_check()
            return self._format_tools(report.get("tools") or [])

        report = manager.health_check()
        return self._format_health(report)

    def _manager(self, ctx: Dict[str, Any]) -> CapabilityManager:
        chat_service = (ctx or {}).get("chat_service")
        runtime = getattr(chat_service, "agent_runtime", None)
        bridge = getattr(chat_service, "mcp_bridge", None)
        return CapabilityManager(
            runtime_getter=lambda: runtime,
            mcp_bridge_getter=lambda: bridge,
        )

    def _extract_capability(self, text: str) -> str:
        raw = str(text or "").strip()
        raw = re.sub(r"^agent\s*(安装计划|install)\s*", "", raw, flags=re.IGNORECASE)
        return raw.strip() or "未指定能力"

    def _format_health(self, report: Dict[str, Any]) -> str:
        lines = [
            "Agent 自检",
            f"- 工具数量: {int(report.get('tool_count') or 0)}",
        ]
        plugins = report.get("plugins") if isinstance(report.get("plugins"), dict) else {}
        if plugins:
            lines.append(
                f"- 插件: 已加载 {int(plugins.get('loaded') or 0)}，失败 {int(plugins.get('failed') or 0)}"
            )
            for item in list(plugins.get("errors") or [])[:5]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  - {item.get('plugin') or 'unknown'}: {str(item.get('error') or '')[:90]}"
                )

        mail = report.get("agent_mail") if isinstance(report.get("agent_mail"), dict) else {}
        if mail:
            if mail.get("configured"):
                cli_status = "存在" if mail.get("cli_exists") else "未找到"
                qq_status = "QQ主人可用" if mail.get("qq_owner_allowed") else "QQ主人不可用"
                lines.append(
                    f"- Agent Mail: CLI {cli_status}，{qq_status}"
                )
            else:
                lines.append(f"- Agent Mail: 未可用 ({mail.get('reason') or 'unknown'})")

        servers = report.get("mcp_servers") or []
        if servers:
            for item in servers:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "mcp")
                connected = bool(item.get("connected"))
                status = "已连接" if connected else "未连接"
                detail = f"，工具 {item.get('tool_count')}" if item.get("tool_count") is not None else ""
                error = str(item.get("error") or "").strip()
                if error:
                    detail += f"，错误: {error}"
                lines.append(f"- MCP: {name} {status}{detail}")
        else:
            lines.append("- MCP: 未配置或暂无状态")
        runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
        if runtime:
            lines.append(
                "- 运行态: "
                + f"QQ={'已接入' if runtime.get('qq_gateway') else '未接入'}，"
                + f"Live2D={'已接入' if runtime.get('live2d') else '未接入'}，"
                + f"屏幕采集={'已接入' if runtime.get('screen_sensor') else '未接入'}"
            )
            if "rust_activity_fresh" in runtime:
                lines.append(
                    "- Rust活动事件: "
                    + ("新鲜" if runtime.get("rust_activity_fresh") else "暂无新鲜事件")
                )
        return "\n".join(lines)

    def _format_location(self, ctx: Dict[str, Any]) -> str:
        chat_service = (ctx or {}).get("chat_service")
        app_root = str(
            (ctx or {}).get("app_root")
            or getattr(chat_service, "app_root", "")
            or ""
        ).strip()
        cwd = str((ctx or {}).get("cwd") or "").strip()
        code_path = str((ctx or {}).get("code_path") or "").strip()
        lines = ["Agent 位置"]
        lines.append(f"主程序代码目录: {app_root or '未知'}")
        lines.append(f"当前工作目录: {cwd or '未知'}")
        lines.append(f"代码助手目标目录: {code_path or app_root or '未知'}")
        return "\n".join(lines)

    def _format_tools(self, tools: Iterable[Dict[str, Any]]) -> str:
        lines = ["Agent 工具目录"]
        count = 0
        for item in tools:
            if not isinstance(item, dict):
                continue
            count += 1
            trigger = str(item.get("trigger") or item.get("name") or "").strip()
            if not trigger:
                continue
            source = str(item.get("source") or "").strip() or "plugin"
            tool_type = str(item.get("type") or "").strip() or "-"
            description = str(item.get("description") or "").strip()
            line = f"- {trigger} [{source}/{tool_type}]"
            if description:
                line += f": {description[:80]}"
            lines.append(line)
        if count == 0:
            lines.append("- 暂无工具")
        return "\n".join(lines)

    def _format_install_plan(self, plan: Dict[str, Any]) -> str:
        capability = str(plan.get("capability") or "").strip() or "未指定能力"
        lines = [
            f"安装计划: {capability}",
            "- 状态: 不会自动执行",
            f"- 动作: {plan.get('action') or 'system.install_dependency'}",
            "- 需要确认: 是" if plan.get("requires_confirmation") else "- 需要确认: 否",
        ]
        notes = plan.get("notes") or []
        for note in notes:
            text = str(note or "").strip()
            if text:
                lines.append(f"- 说明: {text}")
        return "\n".join(lines)
